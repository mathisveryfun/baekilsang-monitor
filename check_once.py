#!/usr/bin/env python3
"""
GitHub Actions용 1회 실행 체크 스크립트
- 4개 백일상 캘린더 체크
- 예약가능 발견 시 텔레그램 알림 + 자동 예약
- 환경변수에서 설정값 읽기
"""

import requests
from bs4 import BeautifulSoup
import os
import json
import time
from datetime import datetime, timedelta

# === 환경변수에서 설정 ===
TELEGRAM_TOKEN = os.environ.get("TELEGRAM_BOT_TOKEN", "")
TELEGRAM_CHAT_ID = os.environ.get("TELEGRAM_CHAT_ID", "")
LOGIN_ID = os.environ.get("LOGIN_ID", "")
LOGIN_PW = os.environ.get("LOGIN_PW", "")

# 신청자 개인정보 환경변수
REQUEST_NAME = os.environ.get("REQUEST_NAME", "")
PHONE1 = os.environ.get("PHONE1", "010")
PHONE2 = os.environ.get("PHONE2", "")
PHONE3 = os.environ.get("PHONE3", "")
CHILD_NAME = os.environ.get("CHILD_NAME", "")
CHILD_BIRTH = os.environ.get("CHILD_BIRTH", "")

BASE_URL = "https://www.ssicare.or.kr"
TARGET_DATE = "2026-06-11"
DATE_PREFIX = "202606"

PRODUCTS = [
    {"sn": 1, "name": "도담상(백일상)"},
    {"sn": 2, "name": "소담상(백일상)"},
    {"sn": 3, "name": "미담상(백일상)"},
    {"sn": 6, "name": "보담상(백일상)"},
]

RESERVATION = {
    "rental_time": "11:00",
    "return_time": "13:00",
    "clothing": "여아한복1",
    "accessory": "여아설유화머리띠1",
}


def send_telegram(message):
    if not TELEGRAM_TOKEN or not TELEGRAM_CHAT_ID:
        print("[WARN] 텔레그램 미설정")
        return False
    try:
        resp = requests.post(
            f"https://api.telegram.org/bot{TELEGRAM_TOKEN}/sendMessage",
            data={"chat_id": TELEGRAM_CHAT_ID, "text": message, "parse_mode": "HTML"},
            timeout=10,
        )
        return resp.status_code == 200
    except Exception as e:
        print(f"[ERROR] 텔레그램: {e}")
        return False


def check_availability(sn):
    url = f"{BASE_URL}/m2/sub4_2_cal.asp?sn={sn}&category=%EB%B0%B1%EC%9D%BC%EC%83%81&datToday={DATE_PREFIX}"
    try:
        resp = requests.get(url, timeout=15)
        # 1. HTTP 상태 코드 검증 (차단되거나 서버 에러인 경우 감지)
        if resp.status_code != 200:
            print(f"[WARN] sn={sn}: HTTP {resp.status_code} 응답 수신")
            return None
        
        # 2. 실제 사이트 본문 검증 (차단 페이지로 리다이렉트 되었거나 빈 페이지인 경우 감지)
        if "육아종합지원센터" not in resp.text:
            print(f"[WARN] sn={sn}: 정상적인 서산시육아종합지원센터 페이지가 아님 (차단 의심)")
            return None

        return f"the_day={TARGET_DATE}" in resp.text
    except Exception as e:
        print(f"[ERROR] sn={sn}: {e}")
        return None


def get_day_korean(date_str):
    days = ["월", "화", "수", "목", "금", "토", "일"]
    return days[datetime.strptime(date_str, "%Y-%m-%d").weekday()]


def attempt_reservation(sn):
    day_kr = get_day_korean(TARGET_DATE)
    session = requests.Session()

    try:
        # 로그인
        session.get(f"{BASE_URL}/member/login.asp", timeout=15)
        session.post(
            f"{BASE_URL}/member/login_ok.asp",
            data={"Userid": LOGIN_ID, "Password": LOGIN_PW},
            timeout=15, allow_redirects=True,
        )

        # 예약 폼 접근
        form_url = (f"{BASE_URL}/m2/sub4_2_write.asp"
                    f"?sn={sn}&the_day={TARGET_DATE}"
                    f"&category=%EB%B0%B1%EC%9D%BC%EC%83%81")
        form_resp = session.get(form_url, timeout=15)

        if "rental_time" not in form_resp.text:
            return False, "예약 폼 로드 실패"

        soup = BeautifulSoup(form_resp.text, "html.parser")
        sday_el = soup.find("input", {"name": "Sday"})
        eday_el = soup.find("input", {"name": "Eday"})
        sday = sday_el["value"] if sday_el else TARGET_DATE
        eday = eday_el["value"] if eday_el else ""

        submit_data = {
            "sn": str(sn),
            "the_day": TARGET_DATE,
            "category": "백일상",
            "agree1": "on",
            "Sday": sday,
            "Eday": eday,
            "rental_time": f"{RESERVATION['rental_time']}({day_kr})",
            "return_time": RESERVATION["return_time"],
            "request_name": REQUEST_NAME,
            "phone1": PHONE1,
            "phone2": PHONE2,
            "phone3": PHONE3,
            "child_chk": f"{CHILD_NAME}/{CHILD_BIRTH}",
            "child_name": CHILD_NAME,
            "child_birth": CHILD_BIRTH,
            "option1": RESERVATION["clothing"],
            "option2": RESERVATION["accessory"],
        }

        print(f"[INFO] 제출 URL: {BASE_URL}/m2/sub4_2_write_ok.asp")
        print(f"[INFO] 데이터: {json.dumps(submit_data, ensure_ascii=False)}")

        result = session.post(
            f"{BASE_URL}/m2/sub4_2_write_ok.asp",
            data=submit_data, timeout=15, allow_redirects=True,
        )

        txt = result.text
        if "완료" in txt or "성공" in txt:
            return True, "예약 성공!"
        elif "이미" in txt or "마감" in txt:
            return False, "이미 마감됨"
        elif "로그인" in txt:
            return False, "로그인 실패"
        else:
            return None, "결과 확인 필요"

    except Exception as e:
        return False, str(e)


def main():
    """28분 동안 30초마다 56회 체크"""
    CHECKS = 56
    INTERVAL = 30
    last_notified_hour = -1  # 중복 알림 방지용
    consecutive_failures = 0
    fail_alert_sent = False

    for round_num in range(1, CHECKS + 1):
        kst_loop = datetime.utcnow() + timedelta(hours=9)
        now = kst_loop.strftime("%Y-%m-%d %H:%M:%S")
        print(f"[{now}] 체크 {round_num}/{CHECKS}")

        # 매시 정각(XX:00~XX:02)에 1회 텔레그램 상태 보고 (1시간 간격)
        if kst_loop.minute < 3 and kst_loop.hour != last_notified_hour:
            last_notified_hour = kst_loop.hour
            send_telegram(
                f"📊 <b>모니터링 정상 작동 중</b>\n"
                f"⏰ {kst_loop.strftime('%Y-%m-%d %H:%M')} (KST)\n"
                f"📅 대상: {TARGET_DATE}\n"
                f"🔄 4개 백일상 모두 대여마감 상태"
            )

        round_failed = True  # 이번 라운드 전체 조회 실패 여부
        for product in PRODUCTS:
            sn = product["sn"]
            name = product["name"]
            available = check_availability(sn)

            if available is None:
                print(f"  {name}: 네트워크 오류")
                continue

            round_failed = False  # 하나라도 정상 응답(True/False)이 오면 정상으로 침

            if available:
                print(f"  {name}: 예약가능 발견!")

                send_telegram(
                    f"🚨🚨🚨 <b>예약가능 발견!</b>\n\n"
                    f"📦 {name} (sn={sn})\n📅 {TARGET_DATE}\n⏰ {now}\n\n"
                    f"🔗 직접 예약:\nhttps://www.ssicare.or.kr/m2/sub4_2_write.asp"
                    f"?sn={sn}&the_day={TARGET_DATE}&category=%EB%B0%B1%EC%9D%BC%EC%83%81\n\n"
                    f"⏳ 자동 예약 시도 중..."
                )

                success, msg = attempt_reservation(sn)

                if success:
                    send_telegram(
                        f"🎉 <b>예약 성공!</b>\n\n📦 {name}\n📅 {TARGET_DATE}\n"
                        f"⏰ 대여 {RESERVATION['rental_time']}\n🔄 반납 {RESERVATION['return_time']}\n"
                        f"👗 {RESERVATION['clothing']}\n🎀 {RESERVATION['accessory']}\n\n✅ 마이페이지에서 확인!"
                    )
                    print(f"  예약 성공!")
                    return
                elif success is None:
                    send_telegram(f"⚠️ <b>결과 확인 필요</b>\n{msg}")
                else:
                    send_telegram(f"❌ <b>자동 예약 실패</b>\n{msg}\n\n직접 예약하세요!")
                    print(f"  {msg}")
            else:
                print(f"  {name}: 대여마감")

        if round_failed:
            consecutive_failures += 1
            print(f"[WARN] {round_num}회차 전체 조회 실패 (누적: {consecutive_failures}회)")
        else:
            consecutive_failures = 0
            fail_alert_sent = False

        # 연속 10회 (약 5분) 이상 전체 실패 시 텔레그램 경고 알림
        if consecutive_failures >= 10 and not fail_alert_sent:
            send_telegram(
                f"⚠️ <b>[경고] 모니터링 접속 실패 발생!</b>\n\n"
                f"서산시육아종합지원센터 사이트 접속이 연속 10회 실패했습니다.\n"
                f"현재 IP 차단(밴) 되었거나 사이트가 점검 중일 가능성이 있습니다.\n"
                f"⏰ 발생 시간: {now}"
            )
            fail_alert_sent = True

        if round_num < CHECKS:
            time.sleep(INTERVAL)

    print("완료")


if __name__ == "__main__":
    main()
