# (c) 2007 Chris AtLee <chris@atlee.ca>
# Licensed under the MIT license:
# http://www.opensource.org/licenses/mit-license.php
#
# Original author: Chris AtLee
#
# Modified by David Ford, 2011-12-6
# added py3 support and encoding
# added pam_end
# added pam_setcred to reset credentials after seeing Leon Walker's remarks
# added byref as well
# use readline to prestuff the getuser input

'''
PAM module for python

Provides an authenticate function that will allow the caller to authenticate
a user against the Pluggable Authentication Modules (PAM) on the system.

Implemented using ctypes, so no compilation is necessary.
'''

import os
import sys
import six

from ctypes import CDLL, POINTER, Structure, CFUNCTYPE, cast, byref, sizeof
from ctypes import c_void_p, c_size_t, c_char_p, c_char, c_int
from ctypes import memmove
from ctypes.util import find_library

__all__ = ['pam']
__version__ = '1.8.5rc2'
__author__ = 'David Ford <david@blue-labs.org>'
__released__ = '2019 November 14'

if sys.version_info < (3, ):
    print('WARNING, Python 2 is EOL and therefore py2 support in this '
          "package is deprecated. It won't be actively checked for"
          'correctness')


# Various constants
PAM_PROMPT_ECHO_OFF = 1
PAM_PROMPT_ECHO_ON = 2
PAM_ERROR_MSG = 3
PAM_TEXT_INFO = 4
PAM_REINITIALIZE_CRED = 8

PAM_TTY = 3


class PamHandle(Structure):
    """wrapper class for pam_handle_t pointer"""
    _fields_ = [("handle", c_void_p)]

    def __init__(self):
        super().__init__()
        self.handle = 0

    def __repr__(self):
        return f"<PamHandle {self.handle}>"


class PamMessage(Structure):
    """wrapper class for pam_message structure"""
    _fields_ = [("msg_style", c_int), ("msg", c_char_p)]

    def __repr__(self):
        return "<PamMessage %i '%s'>" % (self.msg_style, self.msg)


class PamResponse(Structure):
    """wrapper class for pam_response structure"""
    _fields_ = [("resp", c_char_p), ("resp_retcode", c_int)]

    def __repr__(self):
        return "<PamResponse %i '%s'>" % (self.resp_retcode, self.resp)


conv_func = CFUNCTYPE(c_int, c_int, POINTER(POINTER(PamMessage)),
                      POINTER(POINTER(PamResponse)), c_void_p)


class PamConv(Structure):
    """wrapper class for pam_conv structure"""
    _fields_ = [("conv", conv_func), ("appdata_ptr", c_void_p)]


class PamAuthenticator:
    code = 0
    reason = None

    def __init__(self):
        libc = CDLL(find_library("c"))
        libpam = CDLL(find_library("pam"))

        self.calloc = libc.calloc
        self.calloc.restype = c_void_p
        self.calloc.argtypes = [c_size_t, c_size_t]

        # bug #6 (@NIPE-SYSTEMS), some libpam versions don't include this
        # function
        if hasattr(libpam, 'pam_end'):
            self.pam_end = libpam.pam_end
            self.pam_end.restype = c_int
            self.pam_end.argtypes = [PamHandle, c_int]

        self.pam_start = libpam.pam_start
        self.pam_start.restype = c_int
        self.pam_start.argtypes = [c_char_p, c_char_p, POINTER(PamConv),
                                   POINTER(PamHandle)]

        self.pam_acct_mgmt = libpam.pam_acct_mgmt
        self.pam_acct_mgmt.restype = c_int
        self.pam_acct_mgmt.argtypes = [PamHandle, c_int]

        self.pam_set_item = libpam.pam_set_item
        self.pam_set_item.restype = c_int
        self.pam_set_item.argtypes = [PamHandle, c_int, c_void_p]

        self.pam_setcred = libpam.pam_setcred
        self.pam_strerror = libpam.pam_strerror
        self.pam_strerror.restype = c_char_p
        self.pam_strerror.argtypes = [PamHandle, c_int]

        self.pam_authenticate = libpam.pam_authenticate
        self.pam_authenticate.restype = c_int
        self.pam_authenticate.argtypes = [PamHandle, c_int]

    def authenticate(
                self,
                username,
                password,
                service='login',
                encoding='utf-8',
                resetcreds=True):
        authenticate.__annotations = {'username': str,
                                      'password': str,
                                      'service': str,
                                      'encoding': str,
                                      'resetcreds': bool,
                                      'return': bool}
        """username and password authentication for the given service.

        Returns True for success, or False for failure.

        self.code (integer) and self.reason (string) are always stored and may
        be referenced for the reason why authentication failed. 0/'Success'
        will be stored for success.

        Python3 expects bytes() for ctypes inputs.  This function will make
        necessary conversions using the supplied encoding.

        Args:
          username: username to authenticate
          password: password in plain text
          service:  PAM service to authenticate against, defaults to 'login'

        Returns:
          success:  True
          failure:  False
        """

        @conv_func
        def my_conv(n_messages, messages, p_response, app_data):
            """Simple conversation function that responds to any
               prompt where the echo is off with the supplied password"""
            # Create an array of n_messages response objects
            addr = self.calloc(n_messages, sizeof(PamResponse))
            response = cast(addr, POINTER(PamResponse))
            p_response[0] = response
            for i in range(n_messages):
                if messages[i].contents.msg_style == PAM_PROMPT_ECHO_OFF:
                    dst = self.calloc(len(password)+1, sizeof(c_char))
                    memmove(dst, cpassword, len(password))
                    response[i].resp = dst
                    response[i].resp_retcode = 0
            return 0

        # python3 ctypes prefers bytes
        if sys.version_info >= (3, ):
            if isinstance(username, str):
                username = username.encode(encoding)
            if isinstance(password, str):
                password = password.encode(encoding)
            if isinstance(service, str):
                service = service.encode(encoding)

        else:  # py2
            if isinstance(username, six.text_type):
                username = username.encode(encoding)
            if isinstance(password, six.text_type):
                password = password.encode(encoding)
            if isinstance(service, six.text_type):
                service = service.encode(encoding)

        if b'\x00' in username or b'\x00' in password or b'\x00' in service:
            self.code = 4  # PAM_SYSTEM_ERR in Linux-PAM
            self.reason = 'strings may not contain NUL'
            return False

        # do this up front so we can safely throw an exception if there's
        # anything wrong with it
        cpassword = c_char_p(password)

        handle = PamHandle()
        conv = PamConv(my_conv, 0)
        retval = self.pam_start(service, username, byref(conv), byref(handle))

        if retval != 0:
            # This is not an authentication error, something has gone wrong
            # starting up PAM
            self.code = retval
            self.reason = "pam_start() failed"
            return False

        # set the TTY, required when pam_securetty is used and the username
        # root is used note: this is only needed WHEN the pam_securetty.so
        # module is used; for checking /etc/securetty for allowing root
        # logins.  if your application doesn't use a TTY or your pam setup
        # doesn't involve pam_securetty for this auth path, don't worry
        # about it
        #
        # if your app isn't authenticating root with the right password, you
        # may not have the appropriate list of TTYs in /etc/securetty and/or
        # the correct configuration in /etc/pam.d/*
        #
        # if X $DISPLAY is set, use it - otherwise if we have a STDIN tty,
        # get it

        ctty = os.environ.get('DISPLAY')
        if not ctty and os.isatty(0):
            ctty = os.ttyname(0)
        if ctty:
            ctty = c_char_p(ctty.encode(encoding))

            self.pam_set_item(handle, PAM_TTY, ctty)

        retval = self.pam_authenticate(handle, 0)
        auth_success = retval == 0

        if auth_success:
            retval = self.pam_acct_mgmt(handle, 0)
            auth_success = retval == 0

        if auth_success and resetcreds:
            retval = self.pam_setcred(handle, PAM_REINITIALIZE_CRED)

        # store information to inform the caller why we failed
        self.code = retval
        self.reason = self.pam_strerror(handle, retval)
        if sys.version_info >= (3,):
            self.reason = self.reason.decode(encoding)

        if hasattr(self.libpam, 'pam_end'):
            self.pam_end(handle, retval)

        return auth_success


# legacy due to bad naming conventions
pam = PamAuthenticator


def authenticate(*vargs, **dargs):
    """
    Compatibility function for older versions of python-pam.
    """
    return PamAuthenticator().authenticate(*vargs, **dargs)


if __name__ == "__main__":
    import readline
    import getpass

    def input_with_prefill(prompt, text):
        def hook():
            readline.insert_text(text)
            readline.redisplay()

        readline.set_pre_input_hook(hook)

        if sys.version_info >= (3,):
            result = input(prompt)  # nosec (bandit; python2)
        else:
            result = raw_input(prompt)  # noqa:F821

        readline.set_pre_input_hook()

        return result

    pam = PamAuthenticator()

    username = input_with_prefill('Username: ', getpass.getuser())

    # enter a valid username and an invalid/valid password, to verify both
    # failure and success
    pam.authenticate(username, getpass.getpass())
    print('{} {}'.format(pam.code, pam.reason))
