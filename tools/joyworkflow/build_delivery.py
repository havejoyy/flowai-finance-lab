"""Allowlist packaging: never collect credentials, databases or private repository data."""
import hashlib
import zipfile
from pathlib import Path
from release_version import VERSION

FILES = ['release_version.py','data_update.py','report_scope.py','test_report_scope.py','test_data_update.py','windows_launcher.py','build_windows.py','test_windows_portable.py','desktop/windows-build-requirements.txt','desktop/verify_windows.py','docs/手机端方案.md','LICENSE','build_delivery.py', '.gitignore', 'NOTICE.md', 'README.md', 'Start-Windows.cmd', 'Start-macOS.command', 'app.py', 'build_desktop.py', 'data_recovery.py', 'demo.py', 'demo_model.py', 'desktop/JoyWorkflow.swift', 'desktop/build-requirements.lock', 'desktop/certificates/GeoTrustG2TLSCNRSA4096SHA2562022CA1.pem', 'desktop/certificates/README.md', 'desktop_backend.py', 'docs/五层指标库.md', 'docs/单页自动筛选方案.md', 'docs/当前量化规则.md', 'docs/桌面应用使用说明.md', 'engine.py', 'examples/calendar.csv', 'examples/candidates.csv', 'examples/five-layer-complete.txt', 'examples/five-layer.txt', 'examples/prices.csv', 'export_microapp.py', 'five_indicator_library.py', 'five_layer.py', 'five_sources.py', 'industry_data.py', 'market_store.py', 'providers.py', 'screen.html', 'screen_cli.py', 'screening.py', 'screening_data.py', 'screening_indicators.py', 'skill_runner.py', 'skills/youzi-market-research/SKILL.md', 'skills/youzi-market-research/manifest.json', 'skills/youzi-market-research/references/funnel-framework.md', 'strategies/youzi-five-layer-breakout-v1.json', 'templates/five-layer-input.txt', 'test_desktop_recovery.py', 'test_five_indicator_library.py', 'test_five_layer.py', 'test_industry_data.py', 'test_market_store.py', 'test_screening.py', 'test_screening_customization.py', 'test_skill.py', 'test_workflow.py', '自动筛选使用说明.md', '输入资料说明.md']


def main():
    root = Path(__file__).parent
    from skill_runner import resources
    resources()  # Do not package missing or modified Skill resources.
    destination = root / 'dist'
    destination.mkdir(exist_ok=True)
    path = destination / f'JoyWorkflow-local-{VERSION}-source.zip'
    with zipfile.ZipFile(path, 'w', zipfile.ZIP_DEFLATED) as archive:
        for name in FILES:
            data = (root / name).read_bytes()
            info = zipfile.ZipInfo('JoyWorkflow/' + name)
            info.create_system = 3
            info.external_attr = (0o100755 if name.endswith('.command') else 0o100644) << 16
            info.compress_type = zipfile.ZIP_DEFLATED
            archive.writestr(info, data)
    checksum = hashlib.sha256(path.read_bytes()).hexdigest()
    path.with_suffix('.zip.sha256').write_text(checksum + '  ' + path.name + '\n', encoding='utf-8')
    print(str(path) + '\nSHA256 ' + checksum)


if __name__ == '__main__':
    main()
