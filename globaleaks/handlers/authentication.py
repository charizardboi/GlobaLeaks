from functools import wraps
import json
import time
from storm.exceptions import NotOneError

from twisted.internet.defer import inlineCallbacks
from storm.twisted.transact import transact
from cyclone.util import ObjectDict as OD

from globaleaks.models.node import Node
from globaleaks.models.receiver import Receiver
from globaleaks.models.externaltip import WhistleblowerTip
from globaleaks.handlers.base import BaseHandler
from globaleaks.rest.errors import InvalidAuthRequest, InvalidInputFormat, NotAuthenticated
from globaleaks.config import config
from globaleaks.utils.random import random_string

def authenticated(usertype):
    """
    Decorator for authenticated sessions.
    If the user (could be admin/receiver/wb) is not authenticated, return
    a http 401 error.
    Otherwise, update the current session and then fire :param:`method`.
    """
    def userfilter(method):
        authenticated.method = method

    def wrapper(cls, *args, **kwargs):
        if not cls.current_user:
           raise NotAuthenticated
        elif authenticated.role != cls.current_user.role:
            # XXX: eventually change this
            raise NotAuthenticated
        else:
            config.sessions[cls.session_id].timestamp = time.time()
            return authenticated.method(cls, *args, **kwargs)

    authenticated.role = usertype
    return userfilter


class AuthenticationHandler(BaseHandler):
    """
    Login page for administrator
    Extra attributes:
      session_id - current id session for the user
      get_session(username, password) - generates a new session_id
    """
    session_id = None

    def get_current_user(self, session):
        """
        Overrides cyclone's get_current_user method for self.current_user property.
        """
        return config.sessions[self.session_id].id

    def generate_session(self, identifier, role):

        self.session_id = random_string(26, 'a-z,A-Z,0-9')

        # This is the format to preserve sessions in memory
        # Key = session_id, values "last access" "id" "role"

        new_session = OD(
               timestamp=time.time(),
               id=identifier,
               role=role,
        )
        config.sessions[self.session_id] = new_session

        print "+++", new_session, "key", self.session_id
        return self.session_id

    @transact
    def login_wb(self, receipt):
        store = self.get_store()

        print "*** looking for receipt/password", receipt
        try:
            wb = store.find(WhistleblowerTip,
                                   WhistleblowerTip.receipt == unicode(receipt)).one()
        except NotOneError:
            raise InvalidAuthRequest

        if not wb:
            raise InvalidAuthRequest

        return unicode(wb.receipt)

    @transact
    def login_receiver(self, username, password):
        store = self.get_store()

        print "*** looking for receiver", unicode, "with password", password
        try:
            fstreceiver = store.find(Receiver, (Receiver.username == unicode(username), Receiver.password == unicode(password))).one()
        except NotOneError:
            raise InvalidAuthRequest

        if not fstreceiver:
            raise InvalidAuthRequest

        return unicode(fstreceiver.receiver_gus)

    @transact
    def login_admin(self, password):
        store = self.get_store()
        node = store.find(Node).one()
        print "*** Admin password received:", password, "expected password", node.password
        return node.password == password

    @inlineCallbacks
    def post(self):

        try:
            request = json.loads(self.request.body)

            # TODO modify with the validateMessage
            if not all((field in request) for field in  ('username', 'password', 'role')):
                 raise ValueError

            username = request['username']
            password = request['password']
            role = request['role']

            if role == 'admin':
                # username is ignored
                auth = yield self.login_admin(password)
            elif role == 'wb':
                # username is ignored
                auth = yield self.login_wb(password)
            elif role == 'receiver':
                auth = yield self.login_receiver(username, password)
            else:
                raise InvalidInputFormat(role)

            if not auth:
                raise InvalidAuthRequest

            answer = dict({'session_id': self.generate_session(auth, role)})
            self.write(answer)
            self.set_status(200)

        except (InvalidInputFormat, InvalidAuthRequest) as error:

            self.write_error(error)

        except ValueError, e:

            # It's another InvalidInputFormat:
            self.write_error(InvalidInputFormat(e))

        self.finish()

    def delete(self):
        """
        Logout the user.
        """
        if self.current_user:
            del self.current_user
            del config.sessions[self.session_id]

        self.finish()
