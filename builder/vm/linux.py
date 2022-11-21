"""
builder class for kvm vms

- gathers template data
- generates necessary files
- connects to the vm's server and deploys the vm to it
"""
# stdlib
import logging
import os
import random
import shutil
import socket
import string
from crypt import crypt, mksalt, METHOD_SHA512
from typing import Any, Dict, Optional, Tuple
# lib
import opentracing
from jaeger_client import Span
from netaddr import IPAddress, IPNetwork
from paramiko import AutoAddPolicy, RSAKey, SSHClient, SSHException
# local
import settings
import utils
from mixins import LinuxMixin, VMImageMixin


__all__ = [
    'Linux',
]


class Linux(LinuxMixin, VMImageMixin):
    """
    Class that handles the building of the specified VM
    When we get to this point, we can be sure that the VM is a linux VM
    """
    # Keep a logger for logging messages from this class
    logger = logging.getLogger('robot.builders.vm.linux')
    # Keep track of the keys necessary for the template, so we can ensure that all keys are present before building
    template_keys = {
        # the admin password for the vm, unencrypted
        'admin_password',
        # kickstart thing
        'auth',
        # the number of cpus in the vm
        'cpu',
        # the admin password for the vm, pre-crpyted
        'crypted_admin_password',
        # root password encrypted, needed for centos kickstart
        'crypted_root_password',
        # device index and type for nic
        'device_index',
        'device_type',
        # the dns servers for the vm
        'dns',
        # first nic
        'first_nic_primary',
        'first_nic_secondary',
        # the ip address of the host that the VM will be built on
        'host_ip',
        # the sudo password of the host, used to run some commands
        'host_sudo_passwd',
        # the answer_files file of the image used to build the VM
        'image_answer_file_name',
        # the filename of the image used to build the vm
        'image_filename',
        # the os variant of the image used to build the VM
        'image_os_variant',
        # the keyboard layout to use for the vm
        'keyboard',
        # the language of the vm
        'language',
        # The IP Address of the Management interface of the physical Router
        'management_ip',
        # ubuntu netplan support
        'netplan',
        # the non default ip addresses of the vm as nics
        'nics',
        # the path on the host where the network drive is found
        'network_drive_path',
        # the amount of RAM in the VM
        'ram',
        # ssh public key authentication
        'ssh_public_key',
        # storage type (HDD/SSD)
        'storage_type',
        # storages of the vm
        'storages',
        # the timezone of the vm
        'timezone',
        # all subnet vlans numbers list for bridges
        'vlans',
        # an identifier that uniquely identifies the vm
        'vm_identifier',
        # path for vm's .img files located in host
        'vms_path',
    }

    @staticmethod
    def build(vm_data: Dict[str, Any], span: Span) -> bool:
        """
        Commence the build of a vm using the data read from the API
        :param vm_data: The result of a read request for the specified VM
        :param span: The tracing span in use for this build task
        :return: A flag stating whether or not the build was successful
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
            error_msg = f'Template Data Error, the following keys were missing from the VM build data: ' \
                        f'{", ".join(missing_keys)}'
            Linux.logger.error(error_msg)
            vm_data['errors'].append(error_msg)
            span.set_tag('failed_reason', 'template_data_keys_missing')
            return False

        # If everything is okay, commence building the VM
        host_ip = template_data.pop('host_ip')

        # Write necessary files into the network drive
        network_drive_path = settings.KVM_ROBOT_NETWORK_DRIVE_PATH
        path = f'{network_drive_path}/VMs/{vm_data["project"]["id"]}_{vm_id}'
        child_span = opentracing.tracer.start_span('write_files_to_network_drive', child_of=span)
        file_write_success = Linux._generate_network_drive_files(vm_data, template_data, path)
        child_span.finish()

        if not file_write_success:
            # The method will log which part failed, so we can just exit
            span.set_tag('failed_reason', 'network_drive_files_failed_to_write')
            return False

        # Generate the two commands that will be run on the host machine directly
        child_span = opentracing.tracer.start_span('generate_commands', child_of=span)
        bridge_build_cmd, vm_build_cmd = Linux._generate_host_commands(vm_id, template_data)
        child_span.finish()

        # Open a client and run the two necessary commands on the host
        built = False
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

            # Attempt to execute the bridge build commands
            Linux.logger.debug(f'Executing bridge build commands for VM #{vm_id}')

            child_span = opentracing.tracer.start_span('build_bridge', child_of=span)
            stdout, stderr = Linux.deploy(bridge_build_cmd, client, child_span)
            child_span.finish()

            if stdout:
                Linux.logger.debug(f'Bridge build commands for VM #{vm_id} generated stdout.\n{stdout}')
            if stderr:
                Linux.logger.error(f'Bridge build commands for VM #{vm_id} generated stderr.\n{stderr}')
                vm_data['errors'].append(stderr)

            # Now attempt to execute the vm build command
            Linux.logger.debug(f'Executing vm build command for VM #{vm_id}')

            child_span = opentracing.tracer.start_span('build_vm', child_of=span)
            stdout, stderr = Linux.deploy(vm_build_cmd, client, child_span)
            child_span.finish()

            if stdout:
                Linux.logger.debug(f'VM build command for VM #{vm_id} generated stdout.\n{stdout}')
            if stderr:
                Linux.logger.error(f'VM build command for VM #{vm_id} generated stderr.\n{stderr}')
                vm_data['errors'].append(stderr)
            built = 'Domain creation completed' in stdout

        except (OSError, SSHException, TimeoutError):
            error = f'Exception occurred while building VM #{vm_id} in {host_ip}'
            Linux.logger.error(error, exc_info=True)
            vm_data['errors'].append(error)
            span.set_tag('failed_reason', 'ssh_error')
        finally:
            client.close()

        # remove all the files created in network drive
        try:
            shutil.rmtree(path)
        except OSError:
            Linux.logger.warning(f'Failed to remove network drive files for VM #{vm_id}')

        return built

    @staticmethod
    def _get_template_data(vm_data: Dict[str, Any], span: Span) -> Optional[Dict[str, Any]]:
        """
        Given the vm data from the API, create a dictionary that contains all of the necessary keys for the template
        The keys will be checked in the build method and not here, this method is only concerned with fetching the data
        that it can.
        :param vm_data: The data of the VM read from the API
        :param span: Span
        :returns: The data needed for the templates to build a Linux VM
        """
        vm_id = vm_data['id']
        Linux.logger.debug(f'Compiling template data for VM #{vm_id}')
        data: Dict[str, Any] = {key: None for key in Linux.template_keys}

        data['vm_identifier'] = f'{vm_data["project"]["id"]}_{vm_id}'
        data['image_filename'] = vm_data['image']['filename']
        data['management_ip'] = settings.MGMT_IP

        # check if file exists at /mnt/images/KVM/ISOs/
        path = '/mnt/images/KVM/ISOs/'
        child_span = opentracing.tracer.start_span('vm_image_file_download', child_of=span)
        if not Linux.check_image(data['image_filename'], path):
            # download the file
            downloaded, errors = Linux.download_image(data['image_filename'], path)
            if not downloaded:
                for error in errors:
                    Linux.logger.error(error)
                    vm_data['errors'].append(error)
                return None
        child_span.finish()

        data['image_answer_file_name'] = vm_data['image']['answer_file_name']
        data['image_os_variant'] = vm_data['image']['os_variant']
        # RAM is needed in MB for the builder but we take it in GB (1024, not 1000)
        data['ram'] = vm_data['ram'] * 1024
        data['cpu'] = vm_data['cpu']
        data['dns'] = vm_data['dns']

        # Generate encrypted passwords
        admin_password = Linux._password_generator(size=12)
        data['admin_password'] = admin_password
        # Also save the password back to the VM data dict
        vm_data['admin_password'] = admin_password
        data['crypted_admin_password'] = str(crypt(admin_password, mksalt(METHOD_SHA512)))
        root_password = Linux._password_generator(size=128)
        data['crypted_root_password'] = str(crypt(root_password, mksalt(METHOD_SHA512)))
        data['ssh_public_key'] = vm_data['public_key'] if vm_data['public_key'] not in [None, ''] else False

        # Check for the primary storage
        if not any(storage['primary'] for storage in vm_data['storages']):
            error = 'No primary storage drive found. Expected one primary storage drive'
            Linux.logger.error(error)
            vm_data['errors'].append(error)
            return None

        data['storages'] = vm_data['storages']
        data['storage_type'] = vm_data['storage_type']

        # Get the Networking details
        data['vlans'] = []
        data['nics'] = []
        default_ips = []
        default_gateway = None
        default_netmask = None
        default_netmask_int = None
        default_vlan = None

        # The private IPs for the VM will be the one we need to pass to the template
        vm_data['ip_addresses'].reverse()
        ip_addresses = []
        subnets = []
        for ip in vm_data['ip_addresses']:
            if IPAddress(ip['address']).is_private():
                ip_addresses.append(ip)
                subnets.append({
                    'address_range': ip['subnet']['address_range'],
                    'vlan': ip['subnet']['vlan'],
                    'id': ip['subnet']['id'],
                })
        # Removing duplicates
        subnets = [dict(tuple_item) for tuple_item in {tuple(subnet.items()) for subnet in subnets}]
        # sorting nics (each subnet is one nic)
        for subnet in subnets:
            non_default_ips = []
            net = IPNetwork(subnet['address_range'])
            gateway, netmask = str(net.ip), str(net.netmask)
            netmask_int = subnet['address_range'].split('/')[1]
            vlan = str(subnet['vlan'])
            data['vlans'].append(vlan)

            for ip_address in ip_addresses:
                address = ip_address['address']
                if ip_address['subnet']['id'] == subnet['id']:
                    # Pick the default ips if any
                    if vm_data['gateway_subnet'] is not None:
                        if subnet['id'] == vm_data['gateway_subnet']['id']:
                            default_ips.append(address)
                            default_gateway = gateway
                            default_netmask = netmask
                            default_netmask_int = netmask_int
                            default_vlan = vlan
                            continue
                    # else store the non gateway subnet ips
                    non_default_ips.append(address)

            if len(non_default_ips) > 0:
                data['nics'].append({
                    'ips': non_default_ips,
                    'gateway': gateway,
                    'netmask': netmask,
                    'netmask_int': netmask_int,
                    'vlan': vlan,
                })
        # First/Default nic
        data['first_nic_primary'] = {}
        data['first_nic_secondary'] = False
        # in case of default_ips then pick the first ip of default_ips as first_nic_primary
        if len(default_ips) > 0:
            ip0 = default_ips.pop(0)  # removing the first ip
            data['first_nic_primary'] = {
                'ip': ip0,  # taking the first ip
                'gateway': default_gateway,
                'netmask': default_netmask,
                'netmask_int': default_netmask_int,
                'vlan': default_vlan,
            }
            # if any ip left in default_ips would go into first_nic_secondary
            if len(default_ips) > 0:
                data['first_nic_secondary'] = {
                    'ips': default_ips,
                    'gateway': default_gateway,
                    'netmask': default_netmask,
                    'netmask_int': default_netmask_int,
                    'vlan': default_vlan,
                }

        # in case of no default_ips then pick the first ip of first nic as first_nic_primary
        elif len(default_ips) == 0 and len(data['nics']) > 0:
            nic0 = data['nics'].pop(0)  # removing the first nic
            ip0 = nic0['ips'].pop(0)  # removing the first ip
            data['first_nic_primary'] = {
                'ip': ip0,  # taking the first ip
                'gateway': nic0['gateway'],
                'netmask': nic0['netmask'],
                'netmask_int': nic0['netmask_int'],
                'vlan': nic0['vlan'],
            }
            # if any ip left in first nic would go into first_nic_secondary
            if len(nic0['ips']) > 0:
                data['first_nic_secondary'] = nic0
        # set the order for nics in case list order changes
        if len(data['nics']) > 0:
            for i, nic in enumerate(data['nics']):
                nic['order'] = i + 1

        # Add locale data to the VM
        data['keyboard'] = 'ie'
        data['language'] = 'en_IE'
        data['timezone'] = 'Europe/Dublin'

        # Get the ip address of the host
        host_ip = None
        for interface in vm_data['server_data']['interfaces']:
            if interface['enabled'] is True and interface['ip_address'] is not None:
                if IPAddress(str(interface['ip_address'])).version == 6:
                    host_ip = interface['ip_address']
                    break
        if host_ip is None:
            error = f'Host ip address not found for the server # {vm_data["server_id"]}'
            Linux.logger.error(error)
            vm_data['errors'].append(error)
            return None
        data['host_ip'] = host_ip

        # Add the host information to the data
        data['host_sudo_passwd'] = settings.NETWORK_PASSWORD
        data['network_drive_path'] = settings.KVM_HOST_NETWORK_DRIVE_PATH
        data['vms_path'] = settings.KVM_VMS_PATH

        # kickstart thing for old linux oses such as centos7.x or below and rhel7.x or below
        data['auth'] = 'select'
        if vm_data['image']['id'] in [10, 11, 15]:
            data['auth'] = ''
        # device type
        data['device_type'] = 'ens'
        data['device_index'] = 3
        if vm_data['image']['id'] in [7, 10, 11, 15]:
            data['device_type'] = 'eth'
            data['device_index'] = 0
        # netplan in ubuntu 16 complicated so we keep networks
        data['netplan'] = True
        if vm_data['image']['id'] in [6, 7]:
            data['netplan'] = False

        return data

    @staticmethod
    def _generate_network_drive_files(vm_data: Dict[str, Any], template_data: Dict[str, Any], path: str) -> bool:
        """
        Generate and write files into the network drive so they are on the host for the build scripts to utilise.
        Writes the following files to the drive;
            - answer file
            - bridge definition file
        :param vm_data: The data of the VM read from the API
        :param template_data: The retrieved template data for the kvm vm
        :param path: Network drive location to create above files for VM build
        :returns: A flag stating whether or not the job was successful
        """
        vm_id = vm_data['id']
        answer_file_name = template_data['image_answer_file_name']
        # Create a folder by vm_identifier name at network_drive_path/VMs/
        try:
            os.mkdir(path)
        except FileExistsError:
            pass
        except OSError as err:
            error = f'Failed to create directory for VM #{vm_id} at {path}.'
            Linux.logger.error(error, exc_info=True)
            vm_data['errors'].append(f'{error} Error: {err}')
            return False

        # Render and attempt to write the bridge definition file
        for vlan in template_data['vlans']:
            template_name = 'vm/kvm/bridge/definition.j2'
            bridge_def = utils.JINJA_ENV.get_template(template_name).render(vlan=vlan)
            Linux.logger.debug(f'Generated bridge definition file for VM #{vm_id}\n{bridge_def}')
            bridge_def_filename = f'{path}/br{vlan}.yaml'
            try:
                # Attempt to write
                with open(bridge_def_filename, 'w') as f:
                    f.write(bridge_def)
                Linux.logger.debug(
                    f'Successfully wrote bridge definition file for VM #{vm_id} to {bridge_def_filename}',
                )
            except IOError as err:
                error = f'Failed to write bridge definition file for VM #{vm_id} to {bridge_def_filename}'
                Linux.logger.error(error, exc_info=True)
                vm_data['errors'].append(f'{error} Error: {err}')
                return False

        # Render and attempt to write the answer file
        template_name = f'vm/kvm/answer_files/{answer_file_name}.j2'
        answer_file_data = utils.JINJA_ENV.get_template(template_name).render(**template_data)
        Linux.logger.debug(f'Generated answer file for VM #{vm_id}\n{answer_file_data}')
        answer_file_path = f'{path}/{template_data["vm_identifier"]}.cfg'
        try:
            # Attempt to write
            with open(answer_file_path, 'w') as f:
                f.write(answer_file_data)
            Linux.logger.debug(f'Successfully wrote answer file for VM #{vm_id} to {answer_file_path}')
        except IOError as err:
            error = f'Failed to write answer file for VM #{vm_id} to {answer_file_path}'
            Linux.logger.error(error, exc_info=True)
            vm_data['errors'].append(f'{error} Error: {err}')
            return False

        # Return True as all was successful
        return True

    @staticmethod
    def _generate_host_commands(vm_id: int, template_data: Dict[str, Any]) -> Tuple[str, str]:
        """
        Generate the commands that need to be run on the host machine to build the infrastructure
        Generates the following commands;
            - command to build the bridge interface
            - command to build the VM itself
        :param vm_id: The id of the VM being built. Used for log messages
        :param template_data: The retrieved template data for the vm
        :returns: A flag stating whether or not the job was successful
        """
        # Render the bridge build commands
        bridge_cmd = utils.JINJA_ENV.get_template('vm/kvm/bridge/build.j2').render(**template_data)
        Linux.logger.debug(f'Generated bridge build command for VM #{vm_id}\n{bridge_cmd}')

        # Render the VM build command
        vm_cmd = utils.JINJA_ENV.get_template('vm/kvm/commands/build.j2').render(**template_data)
        Linux.logger.debug(f'Generated vm build command for VM #{vm_id}\n{vm_cmd}')

        return bridge_cmd, vm_cmd

    @staticmethod
    def _password_generator(size: int = 12, chars: Optional[str] = None) -> str:
        """
        Returns a string of random characters, useful in generating temporary
        passwords for automated password resets.

        :param size: default=12; override to provide smaller/larger passwords
        :param chars: default=A-Za-z0-9; override to provide more/less diversity
        :return: A password of length 'size' generated randomly from 'chars'
        """
        if chars is None:
            chars = string.ascii_letters + string.digits
        return ''.join(random.choice(chars) for _ in range(size))
