from PyQt6.QtWidgets import QApplication, QMessageBox
import socket
import sys

from info_manager import InfoManager
from device_manager import DeviceManager
from architect_manager import ArchitectManager
from transmission_manager import TransmissionManager
from main_window import MainWindow
import threading
import lpr_recognition_worker


def run_empty_device_client() -> None:
    # 단일 인스턴스 체크: 이미 실행 중이면 메시지 창을 띄우고 종료
    sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    try:
        # 고정 로컬 포트로 바인딩 (예: 39741)
        # 이미 다른 프로세스가 사용 중이면 OSError 발생 → 다른 인스턴스가 실행 중이라고 판단
        sock.bind(("127.0.0.1", 39741))
        sock.listen(1)
    except OSError:
        # 이미 떠 있는 인스턴스가 있을 때는 간단한 알림 창만 띄우고 종료
        app = QApplication.instance() or QApplication(sys.argv)
        QMessageBox.information(
            None,
            "디바이스 클라이언트 실행 중",
            "이미 3.device_client 프로그램이 실행 중입니다.\n두 번째 인스턴스는 종료합니다.",
        )
        sock.close()
        app.quit()
        return

    # 소켓이 GC 되면 포트가 풀리므로, 프로그램이 살아있는 동안 유지되도록
    # QApplication 보다 오래 유지되는 지역 변수로 남겨 둔다.
    # (이 함수 전체가 끝날 때까지 유효)
    app = QApplication(sys.argv)

    # 매니저 초기화
    info_mgr = InfoManager()
    tx_mgr = TransmissionManager(info_mgr)
    dev_mgr = DeviceManager(info_mgr, tx_mgr)
    arch_mgr = ArchitectManager(info_mgr)

    # 장치 매니저 시작(현재는 뼈대만 존재)
    dev_mgr.start()

    # OCR 모델을 백그라운드에서 미리 로드하여 첫 팝업 딜레이 방지
    try:
        from lpr_recognition_worker import LprRecognitionWorker
        t_preload = threading.Thread(target=LprRecognitionWorker.preload_models, daemon=True)
        t_preload.start()
    except Exception as e:
        print(f"Failed to start OCR Preload: {e}")

    win = MainWindow(
        info_manager=info_mgr,
        device_manager=dev_mgr,
        architect_manager=arch_mgr,
        transmission_manager=tx_mgr,
    )
    win.show()

    # 앱 종료 시 정리
    def _cleanup() -> None:
        dev_mgr.stop()
        tx_mgr.close()

    app.aboutToQuit.connect(_cleanup)  # type: ignore[arg-type]

    sys.exit(app.exec())


if __name__ == "__main__":
    run_empty_device_client()
