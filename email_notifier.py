"""
class with static methods that handles sending emails from different tasks on successes or failures
"""
# stdlib
import logging
import os
import smtplib
from email.mime.multipart import MIMEMultipart
from email.mime.text import MIMEText
from email.mime.image import MIMEImage
from json import dumps
from typing import Any, Dict
# local
import settings
import utils

__all__ = [
    'EmailNotifier',
]


class EmailNotifier:
    # A list of image files that need to be attached to the emails
    message_images = ['logo.png', 'twitter.png', 'website.png']

    # ############################################################################################################# #
    #                                                  NOC                                                          #
    # ############################################################################################################# #

    @staticmethod
    def snapshot_failure(snapshot_data: Dict[str, Any], task: str):
        """
        Report any kind of failure to the NOC and developers emails
        """
        logger = logging.getLogger('robot.email_notifier.failure')
        logger.debug(f'Sending failure email for Snapshot #{snapshot_data["id"]} on VM #{snapshot_data["vm"]["id"]}')

        # catch errors
        errors = snapshot_data.pop('errors')
        # Add the pretty printed data blob to the VM
        snapshot_data['data'] = dumps(snapshot_data, indent=2, cls=utils.DequeEncoder)
        # Render the email body
        body = utils.JINJA_ENV.get_template('emails/snapshot_failure.j2').render(
            compute_url=settings.COMPUTE_UI_URL,
            task=task,
            errors=errors,
            **snapshot_data,
        )
        # Format the subject
        subject = settings.SUBJECT_SNAPSHOT_FAIL
        if EmailNotifier._compose_email(settings.SEND_TO_FAIL, subject, body):
            logger.debug(f'Sent failure email for Snapshot #{snapshot_data["id"]} to {settings.SEND_TO_FAIL}.')

    @staticmethod
    def backup_failure(backup_data: Dict[str, Any], task: str):
        """
        Report any kind of failure to the NOC and developers emails
        """
        logger = logging.getLogger('robot.email_notifier.failure')
        logger.debug(f'Sending failure email for Backup #{backup_data["id"]} on VM #{backup_data["vm"]["id"]}')

        # catch errors
        errors = backup_data.pop('errors')
        # Add the pretty printed data blob to the VM
        backup_data['data'] = dumps(backup_data, indent=2, cls=utils.DequeEncoder)
        # Render the email body
        body = utils.JINJA_ENV.get_template('emails/backup_failure.j2').render(
            compute_url=settings.COMPUTE_UI_URL,
            task=task,
            errors=errors,
            **backup_data,
        )
        # Format the subject
        subject = settings.SUBJECT_BACKUP_FAIL
        EmailNotifier._compose_email(settings.SEND_TO_FAIL, subject, body)
        logger.debug(f'Sent failure email for Backup #{backup_data["id"]}.')

    @staticmethod
    def vm_failure(vm_data: Dict[str, Any], task: str):
        """
        Report any kind of failure to the NOC and developers emails
        """
        logger = logging.getLogger('robot.email_notifier.failure')
        logger.debug(f'Sending failure email for VM #{vm_data["id"]}')
        vm_data.pop('admin_password', None)
        # catch errors
        errors = vm_data.pop('errors')
        # Add the pretty printed data blob to the VM
        vm_data['data'] = dumps(vm_data, indent=2, cls=utils.DequeEncoder)
        # Render the email body
        body = utils.JINJA_ENV.get_template('emails/vm_failure.j2').render(
            compute_url=settings.COMPUTE_UI_URL,
            task=task,
            errors=errors,
            **vm_data,
        )
        # Format the subject
        subject = settings.SUBJECT_PROJECT_FAIL
        for email in settings.SEND_TO_FAIL.split(','):
            if EmailNotifier._compose_email(email, subject, body):
                logger.debug(f'Sent failure email for VM #{vm_data["id"]} to {email} to {settings.SEND_TO_FAIL}.')

    @staticmethod
    def virtual_router_failure(virtual_router_data: Dict[str, Any], task: str):
        """
        Report any kind of failure to the NOC and developers emails
        """
        logger = logging.getLogger('robot.email_notifier.virtual_router_failure')
        logger.debug(f'Sending failure email for virtual_router #{virtual_router_data["id"]}')
        # Add the pretty printed data blob to the virtual_router
        virtual_router_data['data'] = dumps(virtual_router_data, indent=2, cls=utils.DequeEncoder)
        # catch errors
        errors = virtual_router_data.pop('errors')
        # Render the email body
        body = utils.JINJA_ENV.get_template('emails/virtual_router_failure.j2').render(
            compute_url=settings.COMPUTE_UI_URL,
            task=task,
            errors=errors,
            **virtual_router_data,
        )
        # Format the subject
        subject = settings.SUBJECT_VIRTUAL_ROUTER_FAIL
        for email in settings.SEND_TO_FAIL.split(','):
            if EmailNotifier._compose_email(email, subject, body):
                logger.debug(f'Sent failure email for virtual router #{virtual_router_data["id"]} to {email}.')

    # ############################################################################################################# #
    #                                               BUILD                                                           #
    # ############################################################################################################# #

    @staticmethod
    def vm_build_success(vm_data: Dict[str, Any]):
        """
        Given a VM's details, render and send a build success email
        """
        logger = logging.getLogger('robot.email_notifier.build_success')
        logger.debug(f'Sending build success email for VM #{vm_data["id"]}')
        # Check that the data contains an email
        emails = vm_data.get('emails', None)
        if emails is None:
            logger.error(f'No email found for VM #{vm_data["id"]}. Sending to {settings.SEND_TO_FAIL} instead.')
            emails = settings.SEND_TO_FAIL.split(',')
        # Render the email body
        body = utils.JINJA_ENV.get_template('emails/vm_build_success.j2').render(
            compute_url=settings.COMPUTE_UI_URL,
            **vm_data,
        )
        # Format the subject
        subject = settings.SUBJECT_VM_SUCCESS
        for email in emails:
            if EmailNotifier._compose_email(email, subject, body):
                logger.debug(f'Sent build success email for VM #{vm_data["id"]} to {email}.')

    @staticmethod
    def vpn_build_success(vpn_data: Dict[str, Any]):
        """
        Given a VPN's details, render and send a build success email
        """
        vpn_id = vpn_data['id']
        logger = logging.getLogger('robot.email_notifier.vpn_build_success')
        logger.debug(f'Sending build success email for VPN #{vpn_id}')
        # Check that the data contains an email
        emails = vpn_data.get('emails', None)
        if emails is None:
            logger.error(f'No email found for VPN #{vpn_id}. Sending to {settings.SEND_TO_FAIL} instead.')
            emails = settings.SEND_TO_FAIL.split(',')
        # Render the email body
        body = utils.JINJA_ENV.get_template('emails/vpn_success.j2').render(
            compute_url=settings.COMPUTE_UI_URL,
            build=True,
            **vpn_data,
        )
        # Format the subject
        subject = settings.SUBJECT_VPN_BUILD_SUCCESS
        for email in emails:
            if EmailNotifier._compose_email(email, subject, body):
                logger.debug(f'Sent build success email for VPN #{vpn_data["id"]} to {email}.')

    @staticmethod
    def vpn_update_success(vpn_data: Dict[str, Any]):
        """
        Given a VPN's details, render and send a update success email
        """
        vpn_id = vpn_data['id']
        logger = logging.getLogger('robot.email_notifier.vpn_update_success')
        logger.debug(f'Sending update success email for VPN #{vpn_id}')
        # Check that the data contains an email
        emails = vpn_data.get('emails', None)
        if emails is None:
            logger.error(f'No email found for VPN #{vpn_id}. Sending to {settings.SEND_TO_FAIL} instead.')
            emails = settings.SEND_TO_FAIL.split(',')
        # Render the email body
        body = utils.JINJA_ENV.get_template('emails/vpn_success.j2').render(
            compute_url=settings.COMPUTE_UI_URL,
            build=False,
            **vpn_data,
        )
        # Format the subject
        subject = settings.SUBJECT_VPN_UPDATE_SUCCESS
        for email in emails:
            if EmailNotifier._compose_email(email, subject, body):
                logger.debug(f'Sent update success email for VPN #{vpn_data["id"]} to {email}.')

    @staticmethod
    def backup_build_failure(backup_data: Dict[str, Any]):
        """
        Given a Backup's details, render and send a build failure email
        """
        logger = logging.getLogger('robot.email_notifier.build_failure')
        logger.debug(f'Sending build failure email for Backup #{backup_data["id"]}.')
        # Check that the data contains an email
        emails = backup_data.get('emails', None)
        if emails is None:
            logger.error(
                f'No email found for Backup #{backup_data["id"]}. Sending to {settings.SEND_TO_FAIL} instead.',
            )
            emails = [settings.SEND_TO_FAIL]
        # Render the email body
        body = utils.JINJA_ENV.get_template('emails/backup_build_failure.j2').render(
            name=backup_data['vm']['name'],
        )
        # Format the subject
        subject = settings.SUBJECT_BACKUP_BUILD_FAIL
        for email in emails:
            EmailNotifier._compose_email(email, subject, body)

        # Also run the generic failure method to pass failures to us
        EmailNotifier.backup_failure(backup_data, 'build')
        logger.debug(f'Sent build failure email for Backup #{backup_data["id"]}.')

    @staticmethod
    def snapshot_build_failure(snapshot_data: Dict[str, Any]):
        """
        Given a Snapshots's details, render and send a build failure email
        """
        logger = logging.getLogger('robot.email_notifier.build_failure')
        logger.debug(f'Sending build failure email for Snapshot #{snapshot_data["id"]}.')
        # Check that the data contains an email
        emails = snapshot_data.get('emails', None)
        if emails is None:
            logger.error(
                f'No email found for Snapshot #{snapshot_data["id"]}. Sending to {settings.SEND_TO_FAIL} instead.',
            )
            emails = [settings.SEND_TO_FAIL]
        # Render the email body
        body = utils.JINJA_ENV.get_template('emails/snapshot_build_failure.j2').render(
            compute_url=settings.COMPUTE_UI_URL,
            **snapshot_data,
        )
        # Format the subject
        subject = settings.SUBJECT_SNAPSHOT_BUILD_FAIL
        for email in emails:
            if EmailNotifier._compose_email(email, subject, body):
                logger.debug(f'Sent build failure email for Snapshot #{snapshot_data["id"]} to {email}.')

        # Also run the generic failure method to pass failures to us
        EmailNotifier.snapshot_failure(snapshot_data, 'build')

    @staticmethod
    def vm_build_failure(vm_data: Dict[str, Any]):
        """
        Given a VM's details, render and send a build failure email
        """
        logger = logging.getLogger('robot.email_notifier.build_failure')
        logger.debug(f'Sending build failure email for VM #{vm_data["id"]}')
        # Check that the data contains an email
        emails = vm_data.get('emails', None)
        if emails is None:
            logger.error(f'No email found for VM #{vm_data["id"]}. Sending to {settings.SEND_TO_FAIL} instead.')
            emails = settings.SEND_TO_FAIL.split(',')
        # Render the email body
        body = utils.JINJA_ENV.get_template('emails/vm_build_failure.j2').render(
            compute_url=settings.COMPUTE_UI_URL,
            **vm_data,
        )
        # Format the subject
        subject = settings.SUBJECT_VM_FAIL
        for email in emails:
            if EmailNotifier._compose_email(email, subject, body):
                logger.debug(f'Sent build failure email for VM #{vm_data["id"]} to {email}.')

        # Also run the generic failure method to pass failures to us
        EmailNotifier.vm_failure(vm_data, 'build')

    # ############################################################################################################# #
    #                                               QUIESCE                                                         #
    # ############################################################################################################# #

    @staticmethod
    def delete_schedule_success(vm_data: Dict[str, Any]):
        """
        Given a VM's details, render and send a delete_schedule success email
        """
        logger = logging.getLogger('robot.email_notifier.delete_schedule_success')
        logger.debug(f'Sending delete scheduled email for VM #{vm_data["id"]}')
        # Check that the data contains an email
        emails = vm_data.get('emails', None)
        if emails is None:
            logger.error(f'No email found for VM #{vm_data["id"]}. Sending to {settings.SEND_TO_FAIL} instead.')
            emails = settings.SEND_TO_FAIL.split(',')
        # Render the email body
        body = utils.JINJA_ENV.get_template('emails/scheduled_delete_success.j2').render(
            compute_url=settings.COMPUTE_UI_URL,
            **vm_data,
        )
        # Format the subject
        subject = settings.SUBJECT_VM_SCHEDULE_DELETE
        for email in emails:
            if EmailNotifier._compose_email(email, subject, body):
                logger.debug(f'Sent delete schedule success email for VM #{vm_data["id"]} to {email}.')

    # ############################################################################################################# #
    #                                           Email Specific Methods                                              #
    # ############################################################################################################# #

    @staticmethod
    def _compose_email(email: str, subject: str, body: str):
        """
        Given an email address, subject and body, compile and send email, returning a success flag
        """
        message = MIMEMultipart('alternative')

        # Populate the headers
        message['subject'] = subject
        message['To'] = email
        message['From'] = settings.EMAIL_HOST_USER
        message['Reply-To'] = settings.EMAIL_REPLY_TO

        # Attach the body of the email
        message.attach(MIMEText(body, 'html'))

        # Attach the images
        for image in EmailNotifier.message_images:
            path = os.path.join(os.getcwd(), 'templates/emails/assets', image)
            with open(path, 'rb') as f:
                mime_image = MIMEImage(f.read())
            mime_image.add_header('Content-ID', f'<{image}>')
            message.attach(mime_image)

        # Send the email
        return EmailNotifier._send(email, message)

    @staticmethod
    def _send(email: str, message: MIMEMultipart):
        """
        Given a receiver's email address and a composed message, attempt to send the message
        """
        logger = logging.getLogger('robot.email_notifier.send_email')
        try:
            server = smtplib.SMTP(settings.EMAIL_HOST, timeout=10)
            # Log in to the server
            server.starttls()
            server.login(settings.EMAIL_HOST_USER, settings.EMAIL_HOST_PASSWORD)
            server.sendmail(settings.EMAIL_HOST_USER, [email], message.as_string())
            server.quit()
            return True
        except Exception:
            logger.error(f'Robot failed to send an email to {email}', exc_info=True)
            return False
