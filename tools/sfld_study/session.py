"""Recommendation/manual/autoplay session for an already authenticated driver.

This does not authenticate an email or expose a public service. The host must
bind its authenticated user to this character and serialize access to SQLite.
The existing controller still owns the live pilot, price and journal checks.
"""
from copy import deepcopy
import secrets
import time
from .schema import StudyError


class PlaySession:
    def __init__(self, controller, subject, *, external=None, clock=time.monotonic):
        controller.store.subject(subject)
        self.controller,self.subject,self.external=controller,subject,external
        self.clock=clock;self.autoplay=False;self._pending=None

    def set_autoplay(self, enabled):
        if type(enabled) is not bool:raise StudyError('Autoplay requires an explicit true or false value.')
        self.autoplay=enabled
        if not enabled:self._pending=None

    def recommend(self):
        self._pending=None
        try:
            proposal=self.controller.recommend(self.subject,external=self.external)
        except BaseException:
            self.autoplay=False
            raise
        token=secrets.token_urlsafe(32)
        self._pending=(token,self.clock(),deepcopy(proposal))
        return {'token':token,'expires_in_seconds':30,'autoplay':self.autoplay,
                'action':deepcopy(proposal['action']),'visible':deepcopy(proposal['visible'])}

    def execute(self, token, *, automatic=False):
        try:
            if automatic and not self.autoplay:raise StudyError('Autoplay is disabled.')
            pending=self._pending
            if not pending or not isinstance(token,str) or not secrets.compare_digest(token,pending[0]):
                raise StudyError('Recommendation is missing or already consumed.')
            self._pending=None  # Consume before attempting any action, including timeouts.
            if not 0<=self.clock()-pending[1]<=30:
                raise StudyError('Recommendation expired; request a fresh one.')
            result=self.controller.step(self.subject,external=self.external,expected=pending[2])
        except BaseException:
            self.autoplay=False
            raise
        return {'executed':True,**result}

    def tick(self):
        try:
            proposal=self.recommend()
            if not self.autoplay:return {'executed':False,**proposal}
            return self.execute(proposal['token'],automatic=True)
        except BaseException:
            self.autoplay=False
            raise
