"""
scrubber class for linux vms

- gathers template data
- connects to the vm's host and runs commands to delete the VM on it
"""
# stdlib
import logging
import socket
from typing import Any, Dict, Optional, Tuple
# lib
import opentracing
from cloudcix.api.iaas import IAAS
from jaeger_client import Span
from netaddr import IPAddress
from paramiko import AutoAddPolicy, RSAKey, SSHClient, SSHException
# local
import settings
import utils
from mixins import LinuxMixin


__all__ = [
    'Linux',
]


class Linux(LinuxMixin):
    """
    Class that handles the scrubbing of the specified VM
    When we get to this point, we can be sure that the VM is a linux VM
    """
    # Keep a logger for logging messages from this class
    logger = logging.getLogger('robot.scrubbers.vm.linux')
    # Keep track of the keys necessary for the template, so we can ensure that all keys are present before scrubbing
    template_keys = {
        # a flag stating whether or not we need to delete the bridge as well (only if there are no more VMs)
        'delete_bridge',
        # the ip address of the host that the VM to scrub is running on
        'host_ip',
        # the sudo password of the host, used to run some commands
        'host_sudo_passwd',
        # storage type (HDD/SSD)
        'storage_type',
        # storages of the vm
        'storages',
        # the vlans that the vm is a part of
        'vlans',
        # an identifier that uniquely identifies the vm
        'vm_identifier',
        # path for vm's .img files located in host
        'vms_path',
    }

    @staticmethod
    def scrub(vm_data: Dict[str, Any], span: Span) -> bool:
        """
        Commence the scrub of a vm using the data read from the API
        :param vm_data: The result of a read request for the specified VM
        :param span: The tracing span for the scrub task
        :return: A flag stating whether or not the scrub was successful
        """
        vm_id = vm_data['id']

        # Generate the necessary template data
        child_span = opentracing.tracer.start_span('generate_template_data', child_of=span)
        template_data = Linux._get_template_data(vm_data, child_span)
        child_span.finish()

        # Check that the data was successfully generated
        if template_data is None:
            error = f'Failed to retrieve template data for VM #{vm_id}.'
            Linux.logger.error(error)
            vm_data['errors'].append(error)
            span.set_tag('failed_reason', 'template_data_failed')
            return False

        # Check that all of the necessary keys are present
        if not all(template_data[key] is not None for key in Linux.template_keys):
            missing_keys = [f'"{key}"' for key in Linux.template_keys if template_data[key] is None]
            error_msg = f'Template Data Error, the following keys were missing from the VM scrub data: ' \
                        f'{", ".join(missing_keys)}.'
            Linux.logger.error(error_msg)
            span.set_tag('failed_reason', 'template_data_keys_missing')
            return False

        # If everything is okay, commence scrubbing the VM
        host_ip = template_data.pop('host_ip')
        delete_bridge = template_data.pop('delete_bridge')

        # Generate the two commands that will be run on the host machine directly
        child_span = opentracing.tracer.start_span('generate_commands', child_of=span)
        bridge_scrub_cmd, vm_scrub_cmd = Linux._generate_host_commands(vm_id, template_data)
        child_span.finish()

        # Open a client and run the two necessary commands on the host
        scrubbed = False
        client = SSHClient()
        client.set_missing_host_key_policy(AutoAddPolicy())
        key = RSAKey.from_private_key_file('/root/.ssh/id_rsa')
        sock = socket.socket(socket.AF_INET6, socket.SOCK_STREAM)
        try:
            # Try connecting to the host and running the necessary commands
            sock.connect((host_ip, 22))
            client.connect(
                hostname=host_ip,
                username='administrator',
                pkey=key,
                timeout=30,
                sock=sock,
            )  # No need for password as it should have keys
            span.set_tag('host', host_ip)

            # Now attempt to execute the vm scrub command
            Linux.logger.debug(f'Executing vm scrub command for VM #{vm_id}')

            child_span = opentracing.tracer.start_span('scrub_vm', child_of=span)
            stdout, stderr = Linux.deploy(vm_scrub_cmd, client, child_span)
            child_span.finish()

            if stdout:
                Linux.logger.debug(f'VM scrub command for VM #{vm_id} generated stdout.\n{stdout}')
                scrubbed = True
            if stderr:
                Linux.logger.error(f'VM scrub command for VM #{vm_id} generated stderr.\n{stderr}')

            # Check if we also need to run the command to delete the bridge
            if delete_bridge:
                Linux.logger.debug(f'Deleting bridge for VM #{vm_id}')

                child_span = opentracing.tracer.start_span('scrub_bridge', child_of=span)
                stdout, stderr = Linux.deploy(bridge_scrub_cmd, client, child_span)
                child_span.finish()

                if stdout:
                    Linux.logger.debug(f'Bridge scrub command for VM #{vm_id} generated stdout\n{stdout}')
                if stderr:
                    Linux.logger.error(f'Bridge scrub command for VM #{vm_id} generated stderr\n{stderr}')

        except (OSError, SSHException, TimeoutError) as err:
            error = f'Exception occurred while scrubbing VM #{vm_id} in {host_ip}.'
            Linux.logger.error(error, exc_info=True)
            vm_data['errors'].append(f'{error} Error: {err}')
            span.set_tag('failed_reason', 'ssh_error')
        finally:
            client.close()
        return scrubbed

    @staticmethod
    def _get_template_data(vm_data: Dict[str, Any], span: Span) -> Optional[Dict[str, Any]]:
        """
        Given the vm data from the API, create a dictionary that contains all of the necessary keys for the template
        The keys will be checked in the build method and not here, this method is only concerned with fetching the data
        that it can.
        :param vm_data: The data of the VM read from the API
        :param span: The tracing span in use for this task. In this method, just pass it to API calls.
        :returns: The data needed for the templates to build a Linux VM
        """
        vm_id = vm_data['id']
        Linux.logger.debug(f'Compiling template data for VM #{vm_id}')
        data: Dict[str, Any] = {key: None for key in Linux.template_keys}

        data['vm_identifier'] = f'{vm_data["project"]["id"]}_{vm_id}'
        data['host_sudo_passwd'] = settings.NETWORK_PASSWORD
        data['storages'] = vm_data['storages']
        data['storage_type'] = vm_data['storage_type']
        data['vms_path'] = settings.KVM_VMS_PATH

        # Get the Networking details
        vlans = [ip['subnet']['vlan'] for ip in vm_data['ip_addresses']]
        data['vlans'] = set(vlans)

        # Get the ip address of the host
        host_ip = None
        for interface in vm_data['server_data']['interfaces']:
            if interface['enabled'] is True and interface['ip_address'] is not None:
                if IPAddress(str(interface['ip_address'])).version == 6:
                    host_ip = interface['ip_address']
                    break
        if host_ip is None:
            error = f'Host ip address not found for the server # {vm_data["server_id"]}.'
            Linux.logger.error(error)
            vm_data['errors'].append(error)
            return None
        data['host_ip'] = host_ip

        child_span = opentracing.tracer.start_span('determine_bridge_deletion', child_of=span)
        data['delete_bridge'] = Linux._determine_bridge_deletion(vm_data, child_span)
        child_span.finish()
        return data

    @staticmethod
    def _generate_host_commands(vm_id: int, template_data: Dict[str, Any]) -> Tuple[str, str]:
        """
        Generate the commands that need to be run on the host machine to scrub the infrastructure
        Generates the following commands;
            - command to scrub the bridge interface
            - command to scrub the VM itself
        :param vm_id: The id of the VM being built. Used for log messages
        :param template_data: The retrieved template data for the vm
        :returns: (bridge_scrub_command, vm_scrub_command)
        """
        # Render the bridge scrub command
        bridge_cmd = utils.JINJA_ENV.get_template('vm/kvm/bridge/scrub.j2').render(**template_data)
        Linux.logger.debug(f'Generated bridge scrub command for VM #{vm_id}\n{bridge_cmd}')

        # Render the VM scrub command
        vm_cmd = utils.JINJA_ENV.get_template('vm/kvm/commands/scrub.j2').render(**template_data)
        Linux.logger.debug(f'Generated vm scrub command for VM #{vm_id}\n{vm_cmd}')

        return bridge_cmd, vm_cmd

    @staticmethod
    def _determine_bridge_deletion(vm_data: Dict[str, Any], span: Span) -> bool:
        """
        Given a VM, determine if we need to delete it's bridge.
        We need to delete the bridge if the VM is the last Linux VM left in the Subnet

        Steps:
            - sort out all the subnets of the VM being deleted
            - List the other private IP Addresses in the same Subnets (excluding this VM's id)
            - List all the VMs pointed to by the vm id fields of the returned (if any)
            - For each VM, get the server_id of the host it is built on
            - List servers by server_id of VMs and type_name KVM
            - If the list is empty return True, else False
        """
        vm_id = vm_data['id']
        # Get the subnet_ids for the private ips configured on VM
        subnet_ids = [ip['subnet']['id'] for ip in vm_data['ip_addresses']]
        subnet_ids = list(set(subnet_ids))  # Removing duplicates

        # Find the other private ip addresses in the subnets
        params = {
            'search[exclude__vm_id]': vm_id,
            'search[subnet_id__in]': subnet_ids,
        }
        subnet_ips = utils.api_list(IAAS.ip_address, params, span=span)

        # List the other VMs in the subnet
        subnet_vm_ids = list(map(lambda ip: ip['vm_id'], subnet_ips))
        subnet_vms = utils.api_list(IAAS.vm, {'search[id__in]': subnet_vm_ids}, span=span)

        # Get the server_id from the VMs and check for KVM hosts
        server_ids = list(set(map(lambda vm: vm['server_id'], subnet_vms)))

        params = {
            'search[id__in]': server_ids,
            'search[type__name]': 'KVM',
        }
        servers = utils.api_list(IAAS.server, params, span=span)

        # If the list of servers is empty we can delete the bridge
        return not bool(servers)
