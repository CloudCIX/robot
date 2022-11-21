# Temporary mapping file for VPNs values required while SRX is still in production

# Strongswan IKE Authentication values
MD5 = 'md5'
SHA1 = 'sha1'
SHA256 = 'sha256'
SHA384 = 'sha384'

# Strongswan IKE DH Groups values
DH_GROUP_1 = 'modp768'
DH_GROUP_2 = 'modp1024'
DH_GROUP_5 = 'modp1536'
DH_GROUP_19 = 'ecp256'
DH_GROUP_20 = 'ecp384'
DH_GROUP_24 = 'modp2048s256'

# Strongswan IPSec Authentication values
HMAC_MD5 = 'md5'
HMAC_SHA1 = 'sha1'
HMAC_SHA384 = 'sha256'

# Strongswan Encryption values
ES128 = 'aes128'
AES192 = 'aes192'
AES256 = 'aes256'
DES = 'des'
DES3 = '3des'
AES128GCM = 'aes128gcm64'
AES192GCM = 'aes192gcm64'
AES256GCM = 'aes256gcm64'

# Strongswan IPSec PFS Group values
PFS_GROUP_1 = 'modp768'
PFS_GROUP_2 = 'modp1024'
PFS_GROUP_5 = 'modp1536'
PFS_GROUP_14 = 'modp2048'
PFS_GROUP_19 = 'ecp256'
PFS_GROUP_20 = 'ecp384'
PFS_GROUP_24 = 'modp2048s256'


# Map SRX value to Strongswan value
IKE_AUTHENTICATION_MAP = {
    'md5': MD5,
    'sha1': SHA1,
    'sha-256': SHA256,
    'sha-384': SHA384,
}

IKE_DH_GROUP_MAP = {
    'group1': DH_GROUP_1,
    'group2': DH_GROUP_2,
    'group5': DH_GROUP_5,
    'group19': DH_GROUP_19,
    'group20': DH_GROUP_20,
    'group24': DH_GROUP_24,
}

IKE_ENCRYPTION_MAP = {
    'aes-128-cbc': ES128,
    'aes-192-cbc': AES192,
    'aes-256-cbc': AES256,
    'des-cbc': DES,
    '3des-cbc': DES3,
}

IPSEC_AUTHENTICATION_MAP = {
    'hmac-md5-96': HMAC_MD5,
    'hmac-sha1-96': HMAC_SHA1,
    'hmac-sha-256-128': HMAC_SHA384,
}


IPSEC_ENCRYPTION_MAP = {
    'aes-128-cbc': ES128,
    'aes-192-cbc': AES192,
    'aes-256-cbc': AES256,
    'des-cbc': DES,
    '3des-cbc': DES3,
    'aes-128-gcm': AES128GCM,
    'aes-192-gcm': AES192GCM,
    'aes-256-gcm': AES256GCM,
}

IPSEC_PFS_GROUP_MAP = {
    'group1': PFS_GROUP_1,
    'group2': PFS_GROUP_2,
    'group5': PFS_GROUP_5,
    'group14': PFS_GROUP_14,
    'group19': PFS_GROUP_19,
    'group20': PFS_GROUP_20,
    'group24': PFS_GROUP_24,
}
