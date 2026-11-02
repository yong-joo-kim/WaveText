"""폴더 선택 창: 폴더와 함께 폴더 안 파일도 보인다."""
from PySide6.QtCore import QDir
from PySide6.QtWidgets import QFileDialog, QListView, QTreeView

from wavetext.ui.dialogs import make_folder_dialog


def _visible_names(dlg, qtbot):
    view = dlg.findChild(QTreeView, "treeView") or dlg.findChild(QListView, "listView")
    model = view.model()
    root = view.rootIndex()
    qtbot.waitUntil(lambda: model.rowCount(root) >= 2, timeout=3000)
    return {model.index(r, 0, root).data() for r in range(model.rowCount(root))}


def test_folder_dialog_shows_folders_and_files(tmp_path, qtbot):
    (tmp_path / "하위폴더").mkdir()
    (tmp_path / "녹취1.wav").write_bytes(b"RIFF")
    dlg = make_folder_dialog(None, "원본 음성 폴더 선택", str(tmp_path))
    qtbot.addWidget(dlg)
    dlg.show()

    assert dlg.fileMode() == QFileDialog.FileMode.Directory
    assert dlg.testOption(QFileDialog.Option.DontUseNativeDialog)
    assert not dlg.testOption(QFileDialog.Option.ShowDirsOnly)
    assert dlg.filter() & QDir.Filter.Files
    assert QDir(dlg.directory()).absolutePath() == QDir(str(tmp_path)).absolutePath()
    assert {"하위폴더", "녹취1.wav"} <= _visible_names(dlg, qtbot)
    dlg.grab().save(str(tmp_path / "folder_dialog.png"))
