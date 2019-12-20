# Robot
Robot is the Regional Cloud Infrastructure Provision Tool of CloudCIX.

# Minimum Hardware Requirements
  -  Single Processor Quad Core
  -  2 x 300GB HDD RAID 1
  -  24 GB RAM
  -  Dual 1Gbps
  -  1 IPMI/iDRAC/iLO Management Port

# Software Requirements
  -  Ubuntu 18.04.2 LTS (GNU/Linux 4.15.0-65-generic x86_64).
  -  Docker 18.09.6, build 481bc77.
  -  Git (Ubuntu comes with git by default but still make sure git is there).

# Servers Preconfiguration
**Robothost**

1. Boot server into IPMI configuration: 

*  Assign the OOB IP address
*  Set user root password
*  Enable virtualisation
*  Set LED screen regionname-robothost

2. Boot server into RAID configuration and create RAID volume (redundant at least RAID1)

3. Start Operating System installation:

*  Assigning the correct management IPv6 address as per “Networks - Management - Specifications And Requirements” section. Set DNS server to a public IPv6 DNS server address
*  Set username as: `administrator`
*  Set hostname to: `regionname-robothost.cloud-name.com`
*  Set Partition to: `Guided - Use entire disk and set UP LVM`  
*  In the software selection tab select: `OpenSSH Server`
*  Finish installation and log in to the server to confirm connectivity

4. Update the server
`sudo apt-get update and sudo apt-get upgrade`


5. Install and run KVM server role and features: 

```
sudo apt install qemu qemu-kvm libvirt-bin  bridge-utils virt-manager

sudo service libvirtd start

sudo update-rc.d libvirtd enable
```

6. Create new bridge br0 on the management interface in /etc/netplan/01-netcfg.yaml file:
network. Make sure to adjust interface name under ethernets section:

```
   version: 2
   renderer: networkd
   ethernets:
 	eno1:
        dhcp4: no
        dhcp6: no
   bridges:
 	br0:
 	  interfaces: [eno2]
 	  dhcp4: no
 	  dhcp6: no
 	  addresses: [ 'xxxx:xxxx:x::2/64' ]
 	  gateway6: xxxx:xxxx:x::1
 	  nameservers:
 		addresses: [ 'xxxx:xxxx:x:xxxx::2' ]
```

7. Install NFS Server role and features:

`sudo apt install nfs-kernel-server`

8. Create the NFS Export directory and set permissions:
```
sudo mkdir -p /var/lib/libvirt/robot-drive
sudo chown nobody:nogroup /var/lib/libvirt/robot-drive
sudo chmod 777 /var/lib/libvirt/robot-drive
```
9. Modify Exports file:
```sudo nano /etc/exports``` and restrict NFS access only to the management network of the region:

```
/var/lib/libvirt/robot-drive xxxx:xxxx:xxxx::/64(rw,sync,no_subtree_check)
```
10. Export the shared directory:

`sudo exportfs -a`

11. Restart service after all the configuration is complete:

`sudo systemctl restart nfs-kernel-server`

12. Copy all the KVM and HYPER-V ISO/Scripts to `/var/lib/libvirt/robot-drive` as this directory will be shared between the members of the region. See the example below (cork01 region NFS settings were modified to allow temporary access from a new region for a file copy).

13. Copy Ubuntu 18.04 ISO image to /var/lib/libvirt/images

```
sudo mkdir /mnt/tmp
sudo mount cork01-robothost.cloud-name.com:/var/lib/libvirt/robot-drive
sudo cp -R /mnt/tmp/* /var/lib/libvirt/robot-drive
sudo umount /mnt/tmp
sudo rm -R /mnt/tmp
```



**Robot (VM)**

1. Use virtmananager and connect to the robothost

2. Build Robot VM:

* ISO - Ubuntu 18.04
* vCPU - 4
* HDD - 60GB
* RAM - 4GB
* Network Selection - br0 virtio
* Boot settings - start VM on host boot


3. Start installation:
*  Assigning the correct management IPv6 address as per “Networks - Management - Specifications And Requirements” section. Set DNS server to a public IPv6 DNS server address
*  Set username as: `administrator`
*  Set hostname to: `regionname-robot.cloud-name.com`
*  Set Partition to: `Guided - Use entire disk and set UP LVM`  
*  In the software selection tab select: `OpenSSH Server`
*  Finish installation and log in to the server to confirm connectivity

4.  Update the server:
`sudo apt-get update && sudo apt-get upgrade -y`

5. Install NFS client:
`sudo apt-get install nfs-common`

6. Create mount point to mount shared NFS drive:
`sudo mkdir -p /mnt/images`

7. Mount the shared directory on the client:
`sudo mount regionname-robothost.cloud-name.com:/var/lib/libvirt/robot-drive /mnt/images`

8. Edit `etc/fstab` file so the NFS share stays mounted:

```
regionname-robothost.cloud-name.com:/var/lib/libvirt/robot-drive /mnt/images nfs defaults 0 0
```

9. Edit `/etc/hosts` file to include DNS records of the robot-host and all the host servers within the region:*

```
IPv6 address	regionname-robothost.cloud-name.com
IPv6 address	regionname-hypervx.cloud-name.com
IPv6 address	regionname-kvmx.cloud-name.com
```

*Or setup DNS records so the hostnames can be resolved from DNS server
 
10. Build a VPN tunnel to Robot for setup. Procedure to be added.


# Robot Software Deployment

> Procedure to be added.
