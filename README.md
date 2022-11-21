# Robot

Robot is an appliance. It is the Regional Cloud Infrastructure Provisioning Tool of CloudCIX. Robot runs Ubuntu 18.04 and KVM.

Robot is built on top of [celery](http://www.celeryproject.org/), using `beat` to handle the periodic tasks, and `workers` to handle the actual infrastructure jobs.

## Minimum Hardware Requirements
  -  Single Processor Quad Core
  -  2 x 300GB HDD RAID 1
  -  24 GB RAM
  -  Dual 1Gbps
  -  1 IPMI/iDRAC/iLO Management Port

## Software Requirements
  -  Ubuntu 18.04.2 LTS (GNU/Linux 4.15.0-65-generic x86_64).
  -  Docker 18.09.6, build 481bc77.
  -  Git (Ubuntu comes with git by default but still make sure git is there).

## Servers Preconfiguration

### Robothost

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

### Robot (VM)

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


### Robot Software Deployment

> Procedure to be added.


## Celery

Robot itself is split into two major parts, both run by celery;

1. `celery beat`
    - This part of Robot handles periodic tasks.
    - Robot has two main periodic tasks;
        - `mainloop`, which runs every 20 seconds, sends requests to the API for requests to build, quiesce, restart and update infrastructure, and passes appropriate tasks to the workers
        - `scrub_loop`, which runs once a day at midnight, does the same except only looks for infrastructure that is ready to be completely deleted, and passes scrub tasks to the workers
2. `celery worker`
    - Each robot has potentially multiple worker containers deployed with it, which handle running the actual infrastructure tasks asyncronously from the mainloop

### Celery Setup

Some things to take note of regarding our set up for Celery;

- `-Ofair` causes celery to distribute tasks to workers that are ready, not as soon as they are received. This means workers that get short running tasks can handle the next task as soon as they are done, instead of piling work onto a worker that is running a long job [see here](https://medium.com/@taylorhughes/three-quick-tips-from-two-years-with-celery-c05ff9d7f9eb)

### Flower
Accessing the IP of the Robot host in the browser will give you access to the Flower instance for the region.

This provides a web UI for monitoring the tasks, queues and workers in Celery for the region.

## Settings and Deployment

### `deployment_sample/`

Copy this directory and rename `deployment`. Files named `region_n` and `region_n+1` represent two regions. These files should be renamed to correspond to the regions you are deploying to.

#### `deployment_sample/docker/`
*`deployment_sample/docker/base.Dockerfile`*: 
1.  Replace `### SSH KEY HOST e.g. github.com ###` with an SSH Key host

*`deployment_sample/docker/region_n.Dockerfile`*: 
1.  Replace `### BASE IMAGE URL ###` with the base image url for Robot
2.  Replace region_n in `deployment/settings/region_n.py` with the name of the region

A Dockerfile is required for each region in your COP

#### `deployment_sample/hosts/`
*`deployment_sample/hosts/region_n.py`*:
1. Replace `### CELERY HOST ####` with the IP Address set in your region's setting file
2. Replace `### REGION NAME ####` with the name of your region
3. Replace `### HOST PASSWORD ###`  with the password to access the CELERY_HOST

A host file is required for each region in your COP

#### `playbook.yml` and `worker-playbook.yml`
1. Replace `### Repository Registry e.g. github.com ###` with the registry for your Docker images
2. Replace `### Repository Username ###` with the username to access to registry
3. Replace `### Repository Password ###` with the password to access to registry
4. Replace `### Image: https://hub.docker.com/r/jaegertracing/jaeger-agent  Version: jaegertracing/jaeger-agent:1.10.1 ###` with the url to the jaeger-agent image in the assigned Docker registry. The Version of the image supported is jaegertracing/jaeger-agent:1.10.1
5. Replace `### LOGSTASH_URL:14267 e.g. logstash.com:14267' ###` with your LOGSTASH_URL
6. Replace `### Image: https://hub.docker.com/r/mher/flower ###` with the url to the flower image in the assigned Docker registry.
7. Replace ` ### Image: https://hub.docker.com/_/rabbitmq  Version: rabbitmq:3.7 ###` with the url to the rabbitmq image in the assigned Docker registry. The Version of the image supported is rabbitmq:3.7
8. Replace  `### BASE IMAGE URL '/robot/{{ env }} e.g. github.com/robot {{ env }} ###` with the url to the base image in the assigned Docker registry e.g. `www.github.com/robot/{{ env }}`

#### `settings/`
*`settings.py.template`*
Complete the `settings.py.template` file and save as the `region_name`.py in the `deployment/settings/` directory.

A settings file is required for each region in your COP

### `ssh_key`
1.  Add the Private ssh-key for ROBOT_RSA generated in the project Rocky when running router_scrub (main setup routine for the Region)

## Email Templates

The Emails in templates/emails/ directory can be personalised.

We recommend you change the following:

**templates/emails/assets**
1. Change logo.png with your logo

**templates/emails/base.j2**
1. line 334: Change link to your Twitter accout or remove
2. line 335 Change link to your website or remove 

Additional changes can also be made to the body of each .j2 file
