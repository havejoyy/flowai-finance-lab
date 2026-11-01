"""Portable launcher contracts tested without requiring native GUI on other OSes."""
import json,sys,tempfile,unittest,sqlite3
from pathlib import Path
from unittest.mock import patch,Mock
from windows_launcher import engine_command,request_stop,owns_service

class PortableTests(unittest.TestCase):
    def test_sqlite_context_commits_rolls_back_and_releases_handle(self):
        from engine import Store
        with tempfile.TemporaryDirectory() as tmp:
            store=Store(tmp)
            with store.connect() as connection:
                connection.execute('CREATE TABLE close_probe (value INTEGER)')
                connection.execute('INSERT INTO close_probe VALUES(1)')
            with self.assertRaises(sqlite3.ProgrammingError):connection.execute('SELECT 1')
            with self.assertRaises(ValueError),store.connect() as connection:
                connection.execute('INSERT INTO close_probe VALUES(2)')
                raise ValueError('rollback')
            with store.connect() as connection:self.assertEqual(connection.execute('SELECT COUNT(*) FROM close_probe').fetchone()[0],1)
            # Windows rejects this rename when a connection is still open.
            renamed=Path(tmp)/'renamed.sqlite3';store.path.rename(renamed);renamed.rename(store.path)

    def test_controller_never_stops_another_process_service(self):
        process=Mock(pid=12);process.poll.return_value=None
        self.assertTrue(owns_service(process,{'pid':12}))
        self.assertFalse(owns_service(process,{'pid':13}))
        process.poll.return_value=0
        self.assertFalse(owns_service(process,{'pid':12}));self.assertFalse(owns_service(None,{'pid':12}))

    def test_frozen_launch_uses_own_executable_and_no_host_python(self):
        with patch.object(sys,'frozen',True,create=True),patch.object(sys,'executable',r'C:\研究工具\JoyWorkflow.exe'):
            cmd=engine_command(Path('path with spaces'))
            self.assertEqual(cmd[0],r'C:\研究工具\JoyWorkflow.exe');self.assertEqual(cmd[1],'--engine');self.assertNotIn('python',cmd[0].lower());self.assertEqual(cmd[-1],'0')
    def test_stop_only_matches_current_instance(self):
        with tempfile.TemporaryDirectory() as tmp:
            root=Path(tmp)
            with patch('windows_launcher.existing_session',return_value={'instance':'new'}):
                self.assertFalse(request_stop(root,{'instance':'old'}));self.assertFalse((root/'desktop-stop.json').exists())
                self.assertTrue(request_stop(root,{'instance':'new'}));self.assertEqual(json.loads((root/'desktop-stop.json').read_text(encoding='utf-8'))['instance'],'new')
    def test_closed_or_untrusted_session_cannot_be_stopped(self):
        with tempfile.TemporaryDirectory() as tmp,patch('windows_launcher.existing_session',return_value=None):self.assertFalse(request_stop(Path(tmp),{'instance':'x'}))
if __name__=='__main__':unittest.main()
