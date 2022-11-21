"""
builder class for virtual_routers

- gathers template data
- generates setconf
- deploys setconf to the chosen router
"""

# stdlib
import logging
import re
from collections import deque
import socket
from typing import Any, Deque, Dict, Optional
# lib
import opentracing
from cloudcix.api.iaas import IAAS
from jaeger_client import Span
from netaddr import IPNetwork
from paramiko import AutoAddPolicy, RSAKey, SSHClient, SSHException
# local
import settings
import utils
from mixins import LinuxMixin
import vpn_mappings


__all__ = [
    'VirtualRouter',
]

ADDRESS_NAME_SUB_PATTERN = re.compile(r'[\.\/:]')


class VirtualRouter(LinuxMixin):
    """
    Class that handles the building of the specified virtual_router
    """
    # Keep a logger for logging messages from this class
    logger = logging.getLogger('robot.builders.virtual_router')
    # Keep track of the keys necessary for the template, so we can check all keys are present before building
    template_keys = {
        # Firewall NFT file name
        'firewall_filename',
        # ID of the IPv4 Floating subnet in the projects network
        'ipv4_floating_subnet_id',
        # if inbound firewall rules
        'inbound_firewall_rules',
        # The local_subnets of the poject in the form  of the network address and mask
        'local_subnets',
        # The IP Address of the Management interface of the physical Router
        'management_ip',
        # A list of NAT rules to be built in the virtual_router
        'nats',
        # if outbound firewall rules
        'outbound_firewall_rules',
        # The CPE for the PODnet box required by VPN tunnels
        'podnet_cpe',
        # The private interface of the firewall
        'private_interface',
        # The id of the Project that owns the virtual_router being built
        'project_id',
        # The public interface of the Router
        'public_interface',
        # Remote working directory
        'remote_path',
        # Temp VPN filename that goes into Podnet at remote_path/temp_vpn_filename
        'temp_vpn_filename',
        # A list of vLans to be built in the virtual_router
        'vlans',
        # A list of VPNs to be built in the virtual_router
        'vpns',
        # The Gateway of the virtual_router's ipv4 floating subnet
        'virtual_router_gateway',
        # The IP Address of the virtual_router
        'virtual_router_ip',
        # The virtual_router IP Subnet Mask, which is needed when making the virtual_router
        'virtual_router_subnet_mask',
        # VPN filename that goes into Podnet /etc/swanctl/conf.d/
        'vpn_filename',
        # The vxLan to use for the project (the project's address id)
        'vxlan',
    }

    @staticmethod
    def build(virtual_router_data: Dict[str, Any], span: Span) -> bool:
        """
        Commence the build of a virtual_router using the data read from the API
        :param virtual_router_data: The result of a read request for the specified virtual_router
        :param span: The tracing span in use for this build task
        :return: A flag stating whether or not the build was successful
        """
        virtual_router_id = virtual_router_data['id']

        # Start by generating the proper dict of data needed by the template
        child_span = opentracing.tracer.start_span('generate_template_data', child_of=span)
        template_data = VirtualRouter._get_template_data(virtual_router_data, child_span)
        child_span.finish()

        # Check that the template data was successfully retrieved
        if template_data is None:
            error = f'Failed to retrieve template data for virtual router #{virtual_router_id}.'
            VirtualRouter.logger.error(error)
            virtual_router_data['errors'].append(error)
            span.set_tag('failed_reason', 'template_data_failed')
            return False

        # Check that all of the necessary keys are present
        if not all(template_data[key] is not None for key in VirtualRouter.template_keys):
            missing_keys = [f'"{key}"' for key in VirtualRouter.template_keys if template_data[key] is None]
            error_msg = f'Template Data Error, the following keys were missing from the virtual_router build data:' \
                        f' {", ".join(missing_keys)}'
            VirtualRouter.logger.error(error_msg)
            virtual_router_data['errors'].append(error_msg)
            span.set_tag('failed_reason', 'template_data_keys_missing')
            return False

        # If everything is okay, commence building the virtual_router
        child_span = opentracing.tracer.start_span('generate_ip_commands', child_of=span)
        build_bash_script = utils.JINJA_ENV.get_template('virtual_router/commands/build.j2').render(**template_data)
        VirtualRouter.logger.debug(
            f'Generated build bash script for virtual_router #{virtual_router_id}\n{build_bash_script}',
        )

        firewall_nft = utils.JINJA_ENV.get_template('virtual_router/features/firewall.j2').render(**template_data)
        VirtualRouter.logger.debug(f'Generated firewall nft for virtual_router #{virtual_router_id}\n{firewall_nft}')

        if len(virtual_router_data['vpns']) > 0:
            vpn_conf = utils.JINJA_ENV.get_template('virtual_router/features/vpn.j2').render(**template_data)
            VirtualRouter.logger.debug(f'Generated vpn conf for virtual_router #{virtual_router_id}\n{vpn_conf}')

        child_span.finish()

        # Log onto PodNet box, write and run network bash script, firewall nft and vpn conf files
        remote_path = template_data.pop('remote_path')
        management_ip = template_data.pop('management_ip')
        built = False

        client = SSHClient()
        client.set_missing_host_key_policy(AutoAddPolicy())
        key = RSAKey.from_private_key_file('/root/.ssh/id_rsa')
        sock = socket.socket(socket.AF_INET6, socket.SOCK_STREAM)
        try:
            # Try connecting to the host and running the necessary commands
            # No need for password as it should have keys
            sock.connect((management_ip, 22))
            client.connect(hostname=management_ip, username='robot', pkey=key, timeout=30, sock=sock)
            sftp = client.open_sftp()
            span.set_tag('host', management_ip)

            # First check if Floating subnet bridge exists on the PodNet netplan directory otherwise create it.
            # this check is only for build task
            ipv4_subnet_id = template_data.pop('ipv4_floating_subnet_id')
            VirtualRouter.logger.debug(
                f'Checking the Floating subnet bridge for Subnet id #{ipv4_subnet_id}',
            )
            floating_bridge_file = f'/etc/netplan/{ipv4_subnet_id}-config.yaml'
            try:
                sftp.open(floating_bridge_file, mode='r')
            except IOError:
                VirtualRouter.logger.debug(
                    f'Floating subnet Bridge not found for id #{ipv4_subnet_id}, so creating the bridge.',
                )
                floating_bridge = utils.JINJA_ENV.get_template(
                    'virtual_router/features/floating_bridge.j2',
                ).render(**template_data, ipv4_floating_subnet_id=ipv4_subnet_id)
                VirtualRouter.logger.debug(
                    f'Generated build floating subnet bridge for Subnet '
                    f'#{ipv4_subnet_id}\n{floating_bridge}',
                )
                temp_floating_bridge_file = f'{remote_path}{ipv4_subnet_id}-config.yaml'
                try:
                    with sftp.open(temp_floating_bridge_file, mode='w', bufsize=1) as yaml:
                        yaml.write(floating_bridge)
                    VirtualRouter.logger.debug(
                        f'Successfully wrote file {temp_floating_bridge_file} to PodNet box#{management_ip}',
                    )
                    # move temp file to netplan dir and apply netplan changes
                    child_span = opentracing.tracer.start_span('apply_netplan_changes', child_of=span)
                    netplan_cmd = f'sudo mv {temp_floating_bridge_file} {floating_bridge_file} && sudo netplan apply'
                    stdout, stderr = VirtualRouter.deploy(netplan_cmd, client, child_span)
                    child_span.finish()
                    if stderr:
                        VirtualRouter.logger.error(
                            f'Applying netplan changes for PodNet #{management_ip} generated stderr: \n{stderr}',
                        )
                        virtual_router_data['errors'].append(stderr)
                    else:
                        VirtualRouter.logger.debug(
                            f'Applying netplan changes for PodNet #{management_ip} generated stdout: \n{stdout}',
                        )

                except IOError as err:
                    VirtualRouter.logger.error(
                        f'Failed to write file {temp_floating_bridge_file} to PodNet box#{management_ip}',
                        exc_info=True,
                    )
                    virtual_router_data['errors'].append(err)
                    return False

            # Secondly, Write Firewall rules file .nft and vpn.conf file(if any) to PodNet box
            child_span = opentracing.tracer.start_span('write_files_to_podnet_box', child_of=span)
            firewall_filename = template_data.pop('firewall_filename')
            try:
                with sftp.open(f'{remote_path}{firewall_filename}', mode='w', bufsize=1) as firewall:
                    firewall.write(firewall_nft)
                VirtualRouter.logger.debug(
                    f'Successfully wrote file {firewall_filename} to PodNet box#{management_ip}',
                )
            except IOError as err:
                VirtualRouter.logger.error(
                    f'Failed to write file {firewall_filename} to PodNet box#{management_ip}',
                    exc_info=True,
                )
                virtual_router_data['errors'].append(err)
                return False

            if len(virtual_router_data['vpns']) > 0:
                temp_vpn_filename = template_data.pop('temp_vpn_filename')
                try:
                    with sftp.open(temp_vpn_filename, mode='w', bufsize=1) as vpn:
                        vpn.write(vpn_conf)
                    VirtualRouter.logger.debug(
                        f'Successfully wrote file {temp_vpn_filename} to PodNet box#{management_ip}',
                    )
                except IOError as err:
                    VirtualRouter.logger.error(
                        f'Failed to write file {temp_vpn_filename} to PodNet box#{management_ip}',
                        exc_info=True,
                    )
                    virtual_router_data['errors'].append(err)
                    return False
            child_span.finish()

            # Finally, Attempt to execute ALL of the virtual router build commands
            # which includes firewall rules and vpns(if any).
            VirtualRouter.logger.debug(
                f'Executing Virtual Router build commands for virtual_router #{virtual_router_id}',
            )
            child_span = opentracing.tracer.start_span('build_virtual_router', child_of=span)
            stdout, stderr = VirtualRouter.deploy(build_bash_script, client, child_span)
            child_span.finish()
            if stderr:
                VirtualRouter.logger.error(
                    f'Virtual Router build commands for virtual_router #{virtual_router_id} generated stderr.'
                    f'\n{stderr}',
                )
                virtual_router_data['errors'].append(stderr)
            else:
                VirtualRouter.logger.debug(
                    f'Virtual Router build commands for virtual_router #{virtual_router_id} generated stdout.'
                    f'\n{stdout}',
                )
                built = True

        except (OSError, SSHException, TimeoutError):
            error = f'Exception occurred while building virtual_router #{virtual_router_id} in {management_ip}'
            VirtualRouter.logger.error(error, exc_info=True)
            virtual_router_data['errors'].append(error)
            span.set_tag('failed_reason', 'ssh_error')
        finally:
            client.close()

        return built

    @staticmethod
    def _get_template_data(virtual_router_data: Dict[str, Any], span: Span) -> Optional[Dict[str, Any]]:
        """
        Given the virtual_router data from the API, create a dictionary that contains all of the necessary keys for the
        template.
        The keys will be checked in the build method and not here, this method is only concerned with fetching the data
        that it can.
        :param virtual_router_data: The data on the virtual_router that was retrieved from the API
        :param span: The tracing span in use for this task. In this method just pass it to API calls
        :returns: Constructed template data, or None if something went wrong
        """
        virtual_router_id = virtual_router_data['id']
        VirtualRouter.logger.debug(f'Compiling template data for virtual_router #{virtual_router_id}')
        data: Dict[str, Any] = {key: None for key in VirtualRouter.template_keys}

        data['project_id'] = project_id = virtual_router_data['project']['id']
        data['vxlan'] = virtual_router_data['project']['address_id']
        # Gather the IP Address and Subnet Mask for the virtual_router
        data['virtual_router_ip'] = virtual_router_data['ip_address']['address']
        data['virtual_router_subnet_mask'] = virtual_router_data['ip_address']['subnet']['address_range'].split('/')[1]
        data['ipv4_floating_subnet_id'] = virtual_router_data['ip_address']['subnet']['id']
        data['virtual_router_gateway'] = virtual_router_data['ip_address']['subnet']['gateway']
        # PODnet CPE required for VPNs
        data['podnet_cpe'] = settings.PODNET_CPE

        # Get the vlans and nat rules for the virtual_router
        vlans: Deque[Dict[str, str]] = deque()
        nats: Deque[Dict[str, str]] = deque()
        local_subnets: Deque[Dict[str, str]] = deque()

        subnets = virtual_router_data['subnets']
        # Add the vlan information to the deque
        for subnet in subnets:
            sub = IPNetwork(subnet['address_range'])
            vlans.append({
                'address_family': sub.version,
                'address_range': subnet['address_range'],
                'vlan': subnet['vlan'],
            })
            local_subnets.append({
                'address_range': f'{sub.network}/{sub.prefixlen}',
            })

        data['vlans'] = vlans
        data['local_subnets'] = local_subnets

        # Check if there are any NAT rules needed in this subnet by filtering ips in subnet that have a public_ip_id
        params = {
            'search[subnet_id__in]': [subnet['id'] for subnet in subnets],
            'search[public_ip_id__isnull]': False,
        }
        child_span = opentracing.tracer.start_span('listing_ip_addresses', child_of=span)
        nat_ips = utils.api_list(IAAS.ip_address, params, span=child_span)
        child_span.finish()
        for ip in nat_ips:
            nats.append({
                'private_address': ip['address'],
                'public_address': ip['public_ip']['address'],
            })
        data['nats'] = nats

        # Router information
        data['management_ip'] = settings.MGMT_IP
        data['private_interface'] = settings.PRIVATE_INF
        data['public_interface'] = settings.PUBLIC_INF

        # Firewall rules
        inbound_firewall_rules: Deque[Dict[str, Any]] = deque()
        outbound_firewall_rules: Deque[Dict[str, Any]] = deque()

        for rule in sorted(virtual_router_data['firewall_rules'], key=lambda fw: fw['order']):
            # logging
            rule['log'] = True if rule['pci_logging'] else rule['debug_logging']
            # Determine if it is IPv4 or IPv6
            rule['address_family'] = IPNetwork(rule['destination']).version

            # Check port and protocol to allow any port for a specific protocol
            if rule['port'] is None:
                rule['port'] = '0-65535'

            if IPNetwork(rule['destination']).is_private():
                inbound_firewall_rules.append(rule)
            else:
                outbound_firewall_rules.append(rule)

        data['inbound_firewall_rules'] = inbound_firewall_rules
        data['outbound_firewall_rules'] = outbound_firewall_rules

        # Finally, get the VPNs for the Project
        vpns: Deque[Dict[str, Any]] = deque()
        params = {'search[virtual_router_id]': virtual_router_id}
        child_span = opentracing.tracer.start_span('listing_vpns', child_of=span)
        virtual_router_vpns = utils.api_list(IAAS.vpn, params, span=child_span)
        child_span.finish()
        for vpn in virtual_router_vpns:
            routes: Deque[Dict[str, str]] = deque()
            local_ts = []
            remote_ts = []
            for route in vpn['routes']:
                local = IPNetwork(str(route['local_subnet']['address_range'])).cidr
                remote = IPNetwork(str(route['remote_subnet'])).cidr
                routes.append({
                    'id': route['id'],
                    'local': local,
                    'remote': remote,
                })
                local_ts.append(str(local))
                remote_ts.append(str(remote))

            vpn['routes'] = routes
            if vpn['traffic_selector']:
                local_ts_set = set(local_ts)
                remote_ts_set = set(remote_ts)
                vpn['local_ts'] = ','.join(local_ts_set)
                vpn['remote_ts'] = ','.join(remote_ts_set)
            else:
                vpn['local_ts'] = '0.0.0.0/0'
                vpn['remote_ts'] = '0.0.0.0/0'
            # version conversion
            vpn['version'] = '1' if vpn['ike_version'] == 'v1-only' else '2'
            # mode
            vpn['aggressive'] = 'yes' if vpn['ike_mode'] == 'aggressive' else 'no'
            # if send_email is true then read VPN for email addresses
            if vpn['send_email']:
                child_span = opentracing.tracer.start_span('reading_vpn', child_of=span)
                vpn['emails'] = utils.api_read(IAAS.vpn, pk=vpn['id'])['emails']
                child_span.finish()
                vpn['srx_vpn_name'] = f'https://{settings.PODNET_CPE}/vrf-{project_id}-{vpn["stif_number"]}-vpn'

            # MAP SRX values to Strongswan values
            vpn['ike_authentication_map'] = vpn_mappings.IKE_AUTHENTICATION_MAP[vpn['ike_authentication']]
            vpn['ike_dh_groups_map'] = vpn_mappings.IKE_DH_GROUP_MAP[vpn['ike_dh_groups']]
            vpn['ike_encryption_map'] = vpn_mappings.IKE_ENCRYPTION_MAP[vpn['ike_encryption']]
            vpn['ipsec_authentication_map'] = vpn_mappings.IPSEC_AUTHENTICATION_MAP[vpn['ipsec_authentication']]
            vpn['ipsec_encryption_map'] = vpn_mappings.IPSEC_ENCRYPTION_MAP[vpn['ipsec_encryption']]
            vpn['ipsec_pfs_groups_map'] = vpn_mappings.IPSEC_PFS_GROUP_MAP[vpn['ipsec_pfs_groups']]

            vpns.append(vpn)
        data['vpns'] = vpns

        # locations and filenames
        # These are here at one place to make changes(if needed) at just here
        data['remote_path'] = '/home/robot/'
        data['firewall_filename'] = f'P{project_id}_firewall.nft'
        data['temp_vpn_filename'] = f'{data["remote_path"]}P{project_id}_vpns.conf'
        data['vpn_filename'] = f'/etc/swanctl/conf.d/P{project_id}_vpns.conf'

        # Store necessary data back in virtual_router data for the email
        virtual_router_data['podnet_cpe'] = data['podnet_cpe']
        virtual_router_data['virtual_router_ip'] = data['virtual_router_ip']
        virtual_router_data['vlans'] = data['vlans']
        virtual_router_data['vpns'] = data['vpns']

        return data
