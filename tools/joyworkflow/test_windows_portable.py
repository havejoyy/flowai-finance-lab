"""Portable launcher contracts tested without requiring native GUI on other OSes."""
import json,sys,tempfile,unittest
from pathlib import Path
from unittest.mock import patch
from windows_launcher import engine_command,request_stop

class PortableTests(unittest.TestCase):
    def test_frozen_launch_uses_own_executable_and_no_host_python(self):
        with patch.object(sys,'frozen',True,create=True),patch.object(sys,'executable',r'C:\研究工具\JoyWorkflow.exe'):
            cmd=engine_command(Path('path with spaces'))
            self.assertEqual(cmd[0],r'C:\研究工具\JoyWorkflow.exe');self.assertEqual(cmd[1],'--engine');self.assertNotIn('python',cmd[0].lower());self.assertEqual(cmd[-1],'0')
    def test_stop_only_matches_current_instance(self):
        with tempfile.TemporaryDirectory() as tmp:
            root=Path(tmp)
            with patch('windows_launcher.existing_session',return_value={'instance':'new'}):
                self.assertFalse(request_stop(root,{'instance':'old'}));self.assertFalse((root/'desktop-stop.json').exists())
                self.assertTrue(request_stop(root,{'instance':'new'}));self.assertEqual(json.loads((root/'desktop-stop.json').read_text())['instance'],'new')
    def test_closed_or_untrusted_session_cannot_be_stopped(self):
        with tempfile.TemporaryDirectory() as tmp,patch('windows_launcher.existing_session',return_value=None):self.assertFalse(request_stop(Path(tmp),{'instance':'x'}))
if __name__=='__main__':unittest.main()
