"""#21419: el barrido recoge todas las carpetas del buzón salvo las de sistema."""

import re
from datetime import timedelta
from urllib.parse import unquote

from django.test import override_settings

from app.models import MailboxSyncCursor, Ticket
from app.services.mailbox_sweep import folder_key
from app.tests_inbound_sweep import FOLDER, T0, FakeMailbox, _Response, _SweepFixture, mail


WELL_KNOWN = {
    'sentitems': 'SENT', 'drafts': 'DRAFTS', 'outbox': 'OUTBOX',
    'deleteditems': 'DELETED', 'junkemail': 'JUNK',
}
ROOT_FOLDERS = [
    {'id': FOLDER, 'displayName': 'Inbox', 'childFolderCount': 1},
    {'id': 'SENT', 'displayName': 'Sent Items', 'childFolderCount': 0},
    {'id': 'DELETED', 'displayName': 'Deleted Items', 'childFolderCount': 1},
    {'id': 'JUNK', 'displayName': 'Junk Email', 'childFolderCount': 0},
    {'id': 'ALARMAS', 'displayName': 'Alarmas', 'childFolderCount': 0},
    {'id': 'REPORTES', 'displayName': 'Reporte Grabaciones', 'childFolderCount': 0},
]
CHILD_FOLDERS = {
    FOLDER: [{'id': 'INBOX-SUB', 'displayName': 'Pendientes', 'childFolderCount': 0}],
    'DELETED': [{'id': 'DELETED-SUB', 'displayName': 'Viejo', 'childFolderCount': 0}],
}


class FolderedMailbox(FakeMailbox):
    def __init__(self, **kwargs):
        super().__init__(**kwargs)
        for folder in ('SENT', 'DELETED', 'DELETED-SUB', 'JUNK', 'ALARMAS', 'REPORTES', 'INBOX-SUB'):
            self.folders.setdefault(folder, [])
        self.fail_well_known = set()
        self.child_requests = []

    def request(self, method, url, headers=None, timeout=None, params=None, json=None):
        path = url.split('/v1.0', 1)[1]
        m = re.search(r'/mailFolders/([^/]+)/childFolders$', path)
        if m:
            parent = unquote(m.group(1))
            self.child_requests.append(parent)
            return _Response(payload={'value': CHILD_FOLDERS.get(parent, [])})
        if path.endswith('/mailFolders'):
            return _Response(payload={'value': ROOT_FOLDERS})
        m = re.search(r'/mailFolders/([a-z]+)$', path)
        if m and m.group(1) != 'Inbox':
            name = m.group(1)
            if name in self.fail_well_known:
                return _Response(500)
            if name in WELL_KNOWN:
                return _Response(payload={'id': WELL_KNOWN[name]})
            return _Response(404)
        return super().request(method, url, headers=headers, timeout=timeout, params=params, json=json)


@override_settings(INBOUND_MARK_READ_ENABLED=False, INBOUND_SWEEP_FOLDERS_CACHE_SECONDS=0)
class SweepAllFoldersTests(_SweepFixture):
    def setUp(self):
        super().setUp()
        self.graph = FolderedMailbox()

    def cursor_folders(self):
        return set(MailboxSyncCursor.objects.values_list('folder_key', flat=True))

    def test_sweeps_every_folder_except_system_ones(self):
        alarm = mail(1, T0 - timedelta(minutes=5), subject='ALARM: "PRO-RDS" in EU (Ireland)')
        report = mail(2, T0 - timedelta(minutes=4), subject='Reporte de grabaciones')
        nested = mail(3, T0 - timedelta(minutes=3))
        sent = mail(4, T0 - timedelta(minutes=2), subject='RE: respuesta nuestra')
        trash = mail(5, T0 - timedelta(minutes=1))
        self.graph.deliver(alarm, folder='ALARMAS')
        self.graph.deliver(report, folder='REPORTES')
        self.graph.deliver(nested, folder='INBOX-SUB')
        self.graph.deliver(sent, folder='SENT')
        self.graph.deliver(trash, folder='DELETED-SUB')

        self.sweep()

        self.assertEqual(self.cursor_folders(), {
            folder_key(FOLDER), folder_key('INBOX-SUB'), folder_key('ALARMAS'), folder_key('REPORTES'),
        })
        for msg in (alarm, report, nested):
            self.assertEqual(self.tickets_for(msg), 1, msg['subject'])
        for msg in (sent, trash):
            self.assertEqual(self.tickets_for(msg), 0, msg['subject'])
        self.assertNotIn('DELETED', self.graph.child_requests)

    def test_new_folders_are_picked_up(self):
        self.sweep()
        ROOT_FOLDERS.append({'id': 'NUEVA', 'displayName': 'Informes SFTP', 'childFolderCount': 0})
        self.addCleanup(ROOT_FOLDERS.pop)
        self.graph.folders['NUEVA'] = []
        msg = mail(6, T0 - timedelta(minutes=1))
        self.graph.deliver(msg, folder='NUEVA')

        self.sweep()

        self.assertIn(folder_key('NUEVA'), self.cursor_folders())
        self.assertEqual(self.tickets_for(msg), 1)

    def test_without_the_system_folders_only_inbox_is_swept(self):
        self.graph.fail_well_known.add('sentitems')
        sent = mail(7, T0 - timedelta(minutes=1))
        self.graph.deliver(sent, folder='SENT')

        self.sweep()

        self.assertEqual(self.cursor_folders(), {folder_key(FOLDER)})
        self.assertEqual(self.tickets_for(sent), 0)

    @override_settings(INBOUND_SWEEP_ALL_FOLDERS=False)
    def test_switch_off_keeps_inbox_only(self):
        alarm = mail(8, T0 - timedelta(minutes=1))
        self.graph.deliver(alarm, folder='ALARMAS')

        self.sweep()

        self.assertEqual(self.cursor_folders(), {folder_key(FOLDER)})
        self.assertEqual(self.tickets_for(alarm), 0)

    def test_message_moved_to_another_folder_is_not_duplicated(self):
        msg = mail(9, T0 - timedelta(minutes=2))
        self.graph.deliver(msg)
        self.sweep()
        self.graph.move(msg['id'], 'ALARMAS', 'AAMkAGY1ODFkYzg1-9-moved')

        self.sweep()

        self.assertEqual(Ticket.objects.filter(subject=msg['subject']).count(), 1)
