"""
small wrapper around the cloudcix token that renews itself when necessary to
ensure that Robot always has a valid token.

Written using the singleton design pattern to ensure that only a single instance
of the class is ever instantiated.
"""
# stdlib
import logging
from datetime import datetime
from typing import cast
# lib
from cloudcix.auth import get_admin_token

__all__ = [
    'Token',
]


class Token:
    """Wrapper for CloudCIX token that renews itself when necessary"""

    # If the token is older than this number of minutes, get a new one
    THRESHOLD = 40

    # Maintain the instance of Token that will be used everywhere
    __instance = None

    def __init__(self):
        # Check to ensure that an instance has not been created yet
        if Token.__instance is not None:
            raise Exception('Trying to instantiate a singleton more than once!')
        # If not, set up everything that we need
        self._token = get_admin_token()
        self._created = datetime.utcnow()
        # Save the instance
        Token.__instance = self

    # Write the method that will retrieve the instance
    @staticmethod
    def get_instance():
        if Token.__instance is None:
            Token()
        return cast(Token, Token.__instance)

    @property
    def token(self) -> str:
        """
        Retrieve the token, refreshing it beforehand if necessary
        """
        if (datetime.utcnow() - self._created).seconds / 60 > self.THRESHOLD:
            # We need to regenerate the token
            self._token = get_admin_token()
            self._created = datetime.utcnow()
            logging.getLogger('robot.cloudcix_token').debug('Generated new token')
        return self._token
