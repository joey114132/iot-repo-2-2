from PyQt6.QtWidgets import (
    QWidget, QVBoxLayout, QHBoxLayout, QLabel, QLineEdit,
    QPushButton, QFormLayout, QGroupBox, QMessageBox
)
from PyQt6.QtCore import pyqtSlot, Qt
import logging

class RfidManagementTab(QWidget):
    def __init__(self, api_client):
        super().__init__()
        self._api = api_client
        self._last_uid = ""
        self._init_ui()

    def _init_ui(self) -> None:
        layout = QVBoxLayout(self)

        # Scanner Group
        scan_group = QGroupBox("RFID Scanner")
        scan_layout = QHBoxLayout()
        self.lbl_scanned_uid = QLabel("마지막 스캔 카드: 없음")
        self.lbl_scanned_uid.setStyleSheet("font-size: 16px; font-weight: bold; color: blue;")
        scan_layout.addWidget(self.lbl_scanned_uid)
        scan_group.setLayout(scan_layout)
        layout.addWidget(scan_group)

        # Registration Group
        reg_group = QGroupBox("입주민(Resident) 등록")
        reg_layout = QFormLayout()

        self.edit_unit = QLineEdit()
        self.edit_name = QLineEdit()
        self.edit_phone = QLineEdit()
        self.edit_plate = QLineEdit()
        self.edit_balance = QLineEdit("0")

        reg_layout.addRow("동/호수 (Unit):", self.edit_unit)
        reg_layout.addRow("이름 (Name):", self.edit_name)
        reg_layout.addRow("전화번호 (Phone):", self.edit_phone)
        reg_layout.addRow("차량번호 (Plate):", self.edit_plate)
        reg_layout.addRow("초기 충전 금액 (Balance):", self.edit_balance)

        self.btn_register = QPushButton("현재 카드로 입주민 등록")
        self.btn_register.clicked.connect(self._on_register_clicked)
        reg_layout.addRow(self.btn_register)

        reg_group.setLayout(reg_layout)
        layout.addWidget(reg_group)

        # Balance Add Group
        bal_group = QGroupBox("입주민 포인트 충전 (기존 입주민)")
        bal_layout = QHBoxLayout()
        self.edit_add_amount = QLineEdit("10000")
        self.btn_add_balance = QPushButton("포인트 충전")
        self.btn_add_balance.clicked.connect(self._on_add_balance_clicked)
        # We need a resident ID to add balance. For simplicity, we assume they scan the card to find the resident, but since we don't have a specific find-by-rfid API endpoint currently, we will just say it applies to the last registered or we can just fetch all residents.
        # Actually, let's just make the user input the resident ID directly or leave it out if it's too complex.
        lbl_bal_help = QLabel("(직전 등록된 입주민에게 추가 충전합니다)")
        
        bal_layout.addWidget(QLabel("충전 금액:"))
        bal_layout.addWidget(self.edit_add_amount)
        bal_layout.addWidget(self.btn_add_balance)
        bal_layout.addWidget(lbl_bal_help)
        bal_group.setLayout(bal_layout)
        layout.addWidget(bal_group)

        layout.addStretch()

        self.last_registered_resident_id = None

    @pyqtSlot(str)
    def on_rfid_scanned(self, uid: str) -> None:
        self._last_uid = uid
        self.lbl_scanned_uid.setText(f"마지막 스캔 카드: {self._last_uid}")

    def _on_register_clicked(self) -> None:
        if not self._last_uid:
            QMessageBox.warning(self, "경고", "먼저 RFID 카드를 스캔해주세요.")
            return

        unit = self.edit_unit.text().strip()
        name = self.edit_name.text().strip()
        phone = self.edit_phone.text().strip()
        plate = self.edit_plate.text().strip()
        balance_str = self.edit_balance.text().strip()

        if not unit or not name or not phone or not plate:
            QMessageBox.warning(self, "경고", "모든 정보를 입력해주세요.")
            return

        try:
            balance = int(balance_str)
        except ValueError:
            QMessageBox.warning(self, "경고", "금액은 숫자로 입력해주세요.")
            return

        try:
            # 1. Create Resident
            resident_req = {
                "unit_number": unit,
                "name": name,
                "phone": phone,
                "car_plate": plate,
                "balance": balance
            }
            res = self._api.create_resident(resident_req)
            resident_id = res.get("id")
            
            if not resident_id:
                raise Exception("Failed to get resident ID from server.")

            self.last_registered_resident_id = resident_id

            # 2. Register RFID
            self._api.register_rfid_card(
                resident_id=resident_id,
                card_uid=self._last_uid,
                description=f"{unit}호 {name}님의 카드"
            )

            QMessageBox.information(self, "성공", f"{name} 입주민이 등록되었습니다 (UID: {self._last_uid}).")
            
            # Clear fields
            self.edit_unit.clear()
            self.edit_name.clear()
            self.edit_phone.clear()
            self.edit_plate.clear()
            self.edit_balance.setText("0")

        except Exception as e:
            QMessageBox.critical(self, "에러", f"입주민 등록 실패:\n{e}")

    def _on_add_balance_clicked(self) -> None:
        if not self.last_registered_resident_id:
            QMessageBox.warning(self, "경고", "최근에 등록/조회된 입주민이 없습니다.")
            return
            
        amt_str = self.edit_add_amount.text().strip()
        try:
            amt = int(amt_str)
            res = self._api.add_resident_balance(self.last_registered_resident_id, amt)
            new_bal = res.get("new_balance", 0)
            QMessageBox.information(self, "성공", f"충전 완료. 현재 잔액: {new_bal}원")
        except ValueError:
            QMessageBox.warning(self, "경고", "금액은 숫자로 입력해주세요.")
        except Exception as e:
            QMessageBox.critical(self, "에러", f"충전 실패:\n{e}")
