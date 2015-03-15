# -*- coding: UTF-8
#
#   cleaning_sched
#   **************
#
# Implementation of the cleaning operations (delete incomplete submission,
# delete expired tips, etc)

import os
from twisted.internet.defer import inlineCallbacks

from globaleaks.jobs.base import GLJob
from globaleaks.jobs.notification_sched import EventLogger, serialize_receivertip, save_event_db
from globaleaks.models import InternalTip, ReceiverFile, InternalFile, Comment, ReceiverTip
from globaleaks.settings import transact, transact_ro, GLSetting
from globaleaks.utils.utility import log, is_expired, datetime_to_ISO8601, ISO8601_to_datetime, utc_dynamic_date

__all__ = ['CleaningSchedule']


class UpcomingExpireEvent(EventLogger):

    def __init__(self):
        EventLogger.__init__(self)
        self.trigger = 'UpcomingExpireTip'

    @transact
    def notify(self, store, tip_id):
        tip= store.find(InternalTip, InternalTip.id == tip_id).one()
        expiring_rtips = store.find(ReceiverTip, ReceiverTip.internaltip_id == tip_id)

        for ertips in expiring_rtips:
            self.do_mail = self.import_receiver(ertip.receiver)
            expiring_tip_desc = serialize_receivertip(ertip)
            self.append_event(tip_info=expiring_tip_desc, subevent_info=None)

@transact_ro
def get_tip_timings(store, new):

    itip_list = store.find(InternalTip, InternalTip.new == new)

    tipinfo_list = []

    for itip in itip_list:
        comment_cnt = store.find(Comment, Comment.internaltip_id == itip.id).count()
        files_cnt = store.find(InternalFile, InternalFile.internaltip_id == itip.id).count()

        tip_timetolive = itip.context.tip_timetolive
        submission_timetolive = itip.context.submission_timetolive

        serialized_tipinfo = {
            'id': itip.id,
            'creation_date': datetime_to_ISO8601(itip.creation_date),
            'expiration_date': datetime_to_ISO8601(itip.expiration_date),
            'upcoming_expiration_date':
                datetime_to_ISO8601(utc_dynamic_date(itip.expiration_date, hours=-48)),
            'tip_life_seconds':  tip_timetolive,
            'submission_life_seconds':  submission_timetolive,
            'files': files_cnt,
            'comments': comment_cnt,
        }

        tipinfo_list.append(serialized_tipinfo)

    return tipinfo_list



@transact
def itip_cleaning(store, tip_id):
    """
    @param tip_id: aim for an InternalTip, and delete them.
    """
    tit = store.find(InternalTip, InternalTip.id == tip_id).one()

    if not tit: # gtfo
        log.err("Requested invalid InternalTip id in itip_cleaning! %s" % tip_id)
        return

    comments = store.find(Comment, Comment.internaltip_id == tip_id)
    log.debug("[-] Removing [%d comments] [%d files] [%d rtips] from an InternalTip" %
        (comments.count(), tit.internalfiles.count(), tit.receivertips.count() ))

    for ifile in tit.internalfiles:
        abspath = os.path.join(GLSetting.submission_path, ifile.file_path)
        ifname = unicode(ifile.name)

        if os.path.isfile(abspath):
            try:
                log.debug("Removing internalfile %s" % abspath)
                os.remove(abspath)
            except OSError as excep:
                log.err("Unable to remove %s: %s" % (abspath, excep.strerror))

        rfiles = store.find(ReceiverFile, ReceiverFile.internalfile_id == ifile.id)
        for rfile in rfiles:
            # The following code must be bypassed if rfile.file_path == ifile.filepath,
            # this mean that is referenced the plaintext file instead having E2E.
            if rfile.file_path == ifile.file_path:
                continue

            abspath = os.path.join(GLSetting.submission_path, rfile.file_path)

            if os.path.isfile(abspath):
                try:
                    log.debug("Removing receiverfile %s" % abspath)
                    os.remove(abspath)
                except OSError as excep:
                    log.err("Unable to remove %s: %s" % (abspath, excep.strerror))

    store.remove(tit)


class CleaningSchedule(GLJob):

    @inlineCallbacks
    def operation(self):
        """
        This function, checks all the InternalTips and their expiration date.
        if expired InternalTips are found, it removes that along with
        all the related DB entries comment and tip related.
        """

        # Reset the exception trackiging variable of GLSetting
        GLSetting.exceptions = {}

        # Check1: check for expired InternalTips (new tips)
        new_tips = yield get_tip_timings(True)
        log.debug("[Tip timings routines / new / expiration ] #%d Tips" % len(new_tips))
        for tip in new_tips:
            if is_expired(ISO8601_to_datetime(tip['expiration_date'])):
                log.info("Deleting an expired Tip (creation date: %s, expiration %s) files %d comments %d" %
                         (tip['creation_date'], tip['expiration_date'], tip['files'], tip['comments']))

                yield itip_cleaning(tip['id'])

        # Check2: check for expired InternalTips (old tips)
        old_tips = yield get_tip_timings(False)
        log.debug("[Tip timings routines / old / expiration upcoming / expire ] #%d Tips" % len(old_tips))
        for tip in old_tips:
            # Check2.1: check if the tip is expired
            if is_expired(ISO8601_to_datetime(tip['expiration_date'])):
                log.info("Deleting an expired Tip (creation date: %s, expiration %s) files %d comments %d" %
                         (tip['creation_date'], tip['expiration_date'], tip['files'], tip['comments']))

                yield itip_cleaning(tip['id'])

            # Check2.2: check if the tip is expiring
            elif is_expired(ISO8601_to_datetime(tip['upcoming_expiration_date'])):
                log.debug("Spotted a Tip matching the upcoming expiration date and "
                          "triggering email notifications")

                expiring_tips_events = UpcomingExpireEvent()
                yield expiring_tips_events.notify(tip['id'])
                yield save_event_db(expiring_tips_events.events)
                yield itip_cleaning(tip['id'])
