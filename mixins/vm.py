"""
mixin class containing methods that are needed by both vm task classes
methods included;
    - a method to generate the drive information for an update
"""
# stdlib
import logging
import os
import shutil
from collections import deque
from typing import Any, Deque, Dict, List, Optional, Tuple
from urllib.error import HTTPError
from urllib.request import urlretrieve
# lib
# local


__all__ = [
    'VMImageMixin',
    'VMUpdateMixin',
]


class VMImageMixin:
    logger: logging.Logger

    @classmethod
    def check_image(cls, filename: str, path: str) -> Optional[bool]:
        """
        Checks if file exists at path
        :param filename: name of the file to search
        :param path: file location
        :return: boolean True for file exists and False for not
        """
        return filename in os.listdir(path)

    @classmethod
    def download_image(cls, filename: str, path: str) -> Tuple[bool, List[str]]:
        """
        This function downloads file_name form downloads.cloudcix.com/robot/ into concerned path at /mnt/images/
        :param filename: name of the file to be downloaded
        :param path: file destination location
        :return: boolean: True for Success and False for Failure
        """
        downloaded = False
        errors: List[str] = []

        # first download file into temp folder and then move to destination,
        # other wise downloading or incomplete download can mislead other vm build task of same image.
        try:
            os.makedirs(f'{path}temp', exist_ok=True)
        except OSError as error:
            errors.append(f'Failed to create temp dir at path {path}, Error:{error}')
            return downloaded, errors

        cls.logger.debug(f'File {filename} not available at {path} so downloading.')
        url = f'https://downloads.cloudcix.com/robot/{filename}'
        try:
            urlretrieve(url, f'{path}temp/{filename}')
            downloaded = True
            cls.logger.debug(f'File {filename} downloaded successfully into {path}{filename}.')
            # move the downloaded file back to destination
            shutil.move(f'{path}temp/{filename}', f'{path}{filename}')
            shutil.chown(f'{path}{filename}', 'nobody', 'nogroup')
        except HTTPError:
            cls.logger.error(f'File {filename} not found at {url}')
            errors.append(f'File {filename} not found at {url}')

        return downloaded, errors


class VMUpdateMixin:
    logger: logging.Logger

    @classmethod
    def fetch_drive_updates(cls, vm_data: Dict[str, Any]) -> Deque[Dict[str, str]]:
        """
        Given a VM's data, generate the data for drives that need to be updated in this update request
        :param vm_data: The data of the VM being updated
        :returns: A Deque of drives for the update request
        """
        vm_id = vm_data['id']
        drives: Deque[Dict[str, str]] = deque()
        storage_changes = vm_data['history'][0]['storage_histories']

        # First one is the latest
        new_storages = storage_changes[0]
        old_storages = {}
        if len(storage_changes) >= 2:
            # second one is the old one.
            old_storages = {storage_changes[1]['id']: storage_change for storage_change in storage_changes[1]}

        # Read the updated storages to determine the changes that were made
        storages = {storage['id']: storage for storage in vm_data['storages']}
        for storage_id, new_storages in new_storages.items():
            # check if storage exits at all
            storage = storages.get(storage_id, None)
            if storage is None:
                cls.logger.error(f'Error fetching Storage #{storage_id} for VM #{vm_id}')
                return drives

            # get the new size
            new_size = storage['gb']

            # get the old size if any
            old_size = '0'
            old_storage = old_storages.get(storage_id, None)
            if old_storage is not None:
                old_size = old_storage['gb_quantity']

            # Append the drive to be updated to the deque
            drives.append({
                'id': storage_id,
                'new_size': new_size,
                'old_size': old_size,
            })

        # Finally, return the generated information
        return drives
