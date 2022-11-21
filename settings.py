import os

ORGANIZATION_URL = os.getenv('ORGANIZATION_URL', 'example.com')
REGION_NAME = os.getenv('POD_NAME', 'pod')
COP_NAME = os.getenv('COP_NAME', 'cop')
COP_ORGANIZATION_URL = os.getenv('COP_ORGANIZATION_URL', 'cop')
COP_PORTAL = os.getenv('COP_PORTAL', 'cop')
SEND_TO_FAIL = os.getenv('SEND_TO_FAIL', '')

CLOUDCIX_API_USERNAME = os.getenv('ROBOT_API_USERNAME', 'user@example.com')
CLOUDCIX_API_KEY = os.getenv('ROBOT_API_KEY', '64_characters_max')
CLOUDCIX_API_PASSWORD = os.getenv('ROBOT_API_PASSWORD', 'pw')
CLOUDCIX_API_URL = f'https://{COP_NAME}.{COP_ORGANIZATION_URL}/'
CLOUDCIX_API_V2_URL = CLOUDCIX_API_URL
CLOUDCIX_API_VERSION = 2

EMAIL_HOST = os.getenv('EMAIL_HOST', 'mail.example.com')
EMAIL_HOST_USER = os.getenv('EMAIL_USER', 'notifications@example.com')
EMAIL_HOST_PASSWORD = os.getenv('EMAIL_PASSWORD', 'email_pw')
EMAIL_PORT = os.getenv('EMAIL_PORT', 25)
EMAIL_REPLY_TO = os.getenv('EMAIL_REPLY_TO', 'no-reply@example.com')

MGMT_IP = os.getenv('ROUTER_MGMT_IP', '2a02:2078:3::10:0:1')
NETWORK_PASSWORD = os.getenv('NETWORK_PASSWORD', 'ntw_pw')

PAM_NAME = os.getenv('PAM_NAME', 'pam')
PAM_ORGANIZATION_URL = os.getenv('PAM_ORGANIZATION_URL', 'example.com')
PODNET_CPE = os.getenv('PODNET_CPE', 'x.x.x.x')
PRIVATE_INF = os.getenv('ROUTER_PRIVATE_INTERFACE', 'eth1')
PUBLIC_INF = os.getenv('ROUTER_PUBLIC_INTERFACE', 'eth0')
VIRTUAL_ROUTERS_ENABLED = True

__all__ = [
    'CELERY_HOST',
    'CLOUDCIX_API_KEY',
    'CLOUDCIX_API_PASSWORD',
    'CLOUDCIX_API_URL',
    'CLOUDCIX_API_USERNAME',
    'CLOUDCIX_API_VERSION',
    'CLOUDCIX_API_V2_URL',
    'COMPUTE_UI_URL',
    'EMAIL_HOST_USER',
    'EMAIL_HOST_PASSWORD',
    'EMAIL_HOST',
    'EMAIL_PORT',
    'EMAIL_REPLY_TO',
    'HYPERV_HOST_NETWORK_DRIVE_PATH',
    'HYPERV_ROBOT_NETWORK_DRIVE_PATH',
    'HYPERV_VMS_PATH',
    'IN_PRODUCTION',
    'CLOUDCIX_INFLUX_DATABASE',
    'CLOUDCIX_INFLUX_PORT',
    'CLOUDCIX_INFLUX_URL',
    'KVM_HOST_NETWORK_DRIVE_PATH',
    'KVM_ROBOT_NETWORK_DRIVE_PATH',
    'KVM_VMS_PATH',
    'LOGSTASH_ENABLE',
    'LOGSTASH_PORT',
    'LOGSTASH_URL',
    'MGMT_IP',
    'NETWORK_DRIVE_URL',
    'NETWORK_PASSWORD',
    'PODNET_CPE',
    'PRIVATE_INF',
    'PUBLIC_INF',
    'ROBOT_ENV',
    'SEND_TO_FAIL',
    'SUBJECT_BACKUP_BUILD_FAIL',
    'SUBJECT_BACKUP_FAIL',
    'SUBJECT_PROJECT_FAIL',
    'SUBJECT_SNAPSHOT_BUILD_FAIL',
    'SUBJECT_SNAPSHOT_FAIL',
    'SUBJECT_VM_FAIL',
    'SUBJECT_VM_SCHEDULE_DELETE',
    'SUBJECT_VM_SUCCESS',
    'SUBJECT_VPN_BUILD_SUCCESS',
    'SUBJECT_VPN_UPDATE_SUCCESS',
    'SUBJECT_VIRTUAL_ROUTER_FAIL',
    'VIRTUAL_ROUTERS_ENABLED',
]

"""
Robot Settings
"""
CELERY_HOST = 'rabbitmq'

# Password for connecting to windows servers
NETWORK_PASSWORD = NETWORK_PASSWORD

# Flag to state whether VIRTUAL_ROUTERs are enabled or not
VIRTUAL_ROUTERS_ENABLED = VIRTUAL_ROUTERS_ENABLED

# Flag stating whether a region is in production or not
IN_PRODUCTION = True

"""
Email settings
"""
# Compute UI URL - Required in Email Templates
COMPUTE_UI_URL = f'https://{COP_PORTAL}.{COP_ORGANIZATION_URL}/compute/'

# Reply-To Email Address
EMAIL_REPLY_TO = f'{ORGANIZATION_URL} <{EMAIL_REPLY_TO}>'

# Email to send build fail emails to
SEND_TO_FAIL = SEND_TO_FAIL

# Subject for Backups build fails
SUBJECT_BACKUP_BUILD_FAIL = f'[{ORGANIZATION_URL}] Your Backup has failed to build.'

# Subject for Backup fails
SUBJECT_BACKUP_FAIL = f'[{ORGANIZATION_URL}] Backup Failure Occurred!'

# Subject for Project build fail Emails
SUBJECT_PROJECT_FAIL = f'[{ORGANIZATION_URL}] VM Failure Occurred!'

# Subject for Snapshot build fail Emails
SUBJECT_SNAPSHOT_BUILD_FAIL = f'[{ORGANIZATION_URL}] Your Snapshot has failed to build.'

# Subject for Snapshot fail Emails
SUBJECT_SNAPSHOT_FAIL = f'[{ORGANIZATION_URL}] Snapshot Failure Occurred!'

# Subject for VM build fail Emails
SUBJECT_VM_FAIL = f'[{ORGANIZATION_URL}] Your VM  has failed to build.'

# Subject for VM scheduled to be deleted
SUBJECT_VM_SCHEDULE_DELETE = f'[{ORGANIZATION_URL}] Your VM has been scheduled for deletion!'

# Subject for VM build success Emails
SUBJECT_VM_SUCCESS = f'[{ORGANIZATION_URL}] Your VM has been built successfully!'

# Subject for VPN tunnel build success Emails
SUBJECT_VPN_BUILD_SUCCESS = f'[{ORGANIZATION_URL}] Your VPN Tunnel has been built successfully!'

# Subject for VPN tunnel update success Emails
SUBJECT_VPN_UPDATE_SUCCESS = f'[{ORGANIZATION_URL}] Your VPN Tunnel has been updated successfully!'

# Subject for VIRTUAL_ROUTER build fail Emails
SUBJECT_VIRTUAL_ROUTER_FAIL = f'[{ORGANIZATION_URL}] Virtual Router Failure Occurred!'

# Env (used in log messages and other things)
ROBOT_ENV = f'{REGION_NAME}'

"""
Configuration settings
"""
# KVM path
KVM_ROBOT_NETWORK_DRIVE_PATH = '/mnt/images/KVM'
KVM_HOST_NETWORK_DRIVE_PATH = '/var/lib/libvirt/ISOs/KVM'
# KVM vms path
KVM_VMS_PATH = '/var/lib/libvirt/images/'

# HyperV path
HYPERV_ROBOT_NETWORK_DRIVE_PATH = '/mnt/images/HyperV'
HYPERV_HOST_NETWORK_DRIVE_PATH = '/var/lib/libvirt/ISOs/HyperV'
# HyperV vms path
HYPERV_VMS_PATH = r'D:\HyperV\\'
# Nas drive mount url
NETWORK_DRIVE_URL = f'\\\\robot.{REGION_NAME}.{ORGANIZATION_URL}\\etc\\cloudcix\\robot'


CLOUDCIX_INFLUX_PORT = 443

LOGSTASH_ENABLE = os.getenv('LOGSTASH_ENABLE', 'false').lower() == 'true'
LOGSTASH_PORT = os.getenv('LOGSTASH_PORT', 5044)


if f'{PAM_NAME}.{PAM_ORGANIZATION_URL}' == 'support.cloudcix.com':
    LOGSTASH_ENABLE = True
    CLOUDCIX_INFLUX_URL = 'influxdb.support.cloudcix.com'
    LOGSTASH_URL = 'logstash.support.cloudcix.com'
    CLOUDCIX_INFLUX_DATABASE = 'robot'
else:
    LOGSTASH_URL = os.getenv('LOGSTASH_URL', '')
    CLOUDCIX_INFLUX_URL = os.getenv('INFLUX_URL', '')
    CLOUDCIX_INFLUX_DATABASE = os.getenv('INFLUX_DATABASE', '')

"""
Backup Settings
"""
# HyperV primary backup location
HYPERV_PRIMARY_BACKUP_STORAGE_PATH = 'P:\\'
# HyperV secondary backup location
HYPERV_SECONDARY_BACKUP_STORAGE_PATH = 'S:\\'
# KVM primary backup location
KVM_PRIMARY_BACKUP_STORAGE_PATH = '/mnt/backup-p/'
# KVM secondary backup location
KVM_SECONDARY_BACKUP_STORAGE_PATH = '/mnt/backup-s/'
