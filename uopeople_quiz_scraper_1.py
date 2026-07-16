#!/usr/bin/env python3
"""
UoPeople D2L Brightspace - Attempted Quiz Scraper
--------------------------------------------------
Logs into your own UoPeople student portal account, opens the "attempted
quizzes" list for a given course + week, walks through every "Attempt N"
review page, extracts the 5 MCQ/True-False questions (with the option that
is marked as the correct answer), de-duplicates repeated questions across
attempts, and saves everything into a nicely formatted study PDF.

This only ever uses YOUR OWN login credentials and only ever visits pages
that your own account is already authorized to view (your own quiz
submission history).

WHY SELENIUM: The D2L login form's "Log In" button is a plain
<button type="button">, not a submit button - the actual sign-in is
performed entirely by the site's own JavaScript (D2L.LP.Web.Authentication.
Xsrf.Init etc.), which attaches an anti-CSRF token that isn't present
anywhere in the static HTML. A plain requests.post() to the form's action
URL skips that handshake, so the server quietly hands back a fresh
anonymous session even though the response page looks "logged in". Driving
a real browser sidesteps this entirely, since the site's own JS runs
exactly as it does when you log in by hand.

Setup:
    pip install selenium beautifulsoup4 reportlab
    (You need Google Chrome installed. Selenium 4.6+ auto-downloads a
    matching chromedriver for you the first time it runs.)

Usage:
    python uopeople_quiz_scraper.py

You will be prompted for:
    - Username (UoPeople ID)
    - Password
    - Course org unit code (the "ou" number in the course URL, e.g. 8455)
    - Week number(s): a single number 1-8, a comma separated list (e.g. 1,2,5),
      or "all" for weeks 1-8.
"""

import re
import sys
import time
import getpass
import html as html_lib
from dataclasses import dataclass, field
from typing import List

from bs4 import BeautifulSoup

from selenium import webdriver
from selenium.webdriver.chrome.options import Options
from selenium.webdriver.common.by import By
from selenium.webdriver.support.ui import WebDriverWait
from selenium.webdriver.support import expected_conditions as EC
from selenium.common.exceptions import TimeoutException, NoSuchElementException

from reportlab.lib.pagesizes import LETTER
from reportlab.lib.units import mm
from reportlab.lib import colors
from reportlab.lib.styles import getSampleStyleSheet, ParagraphStyle
from reportlab.platypus import (
    SimpleDocTemplate, Paragraph, Spacer, Table, TableStyle, Flowable,
    KeepTogether
)

BASE_URL = "https://learn.uopeople.edu"
LOGIN_URL = f"{BASE_URL}/d2l/login"

# Week 1 always corresponds to quiz-id (qi) 10185 in the observed pattern,
# each subsequent week increments qi by 1.
WEEK1_QI = 10185

PAGE_LOAD_TIMEOUT = 25


# --------------------------------------------------------------------------
# Data model
# --------------------------------------------------------------------------

@dataclass
class Option:
    text: str
    correct: bool = False


@dataclass
class Question:
    text: str
    options: List[Option] = field(default_factory=list)
    source: str = ""  # e.g. "Week 1 - Attempt 3"

    def dedup_key(self):
        norm_q = re.sub(r"\s+", " ", self.text).strip().lower()
        norm_opts = tuple(sorted(
            re.sub(r"\s+", " ", o.text).strip().lower() for o in self.options
        ))
        return (norm_q, norm_opts)


# --------------------------------------------------------------------------
# Browser setup
# --------------------------------------------------------------------------

def make_driver(headless: bool = False) -> webdriver.Chrome:
    options = Options()
    if headless:
        options.add_argument("--headless=new")
    options.add_argument("--window-size=1400,1000")
    options.add_argument("--disable-blink-features=AutomationControlled")
    options.add_experimental_option("excludeSwitches", ["enable-automation"])
    options.add_experimental_option("useAutomationExtension", False)
    driver = webdriver.Chrome(options=options)
    driver.set_page_load_timeout(PAGE_LOAD_TIMEOUT)
    return driver


# --------------------------------------------------------------------------
# Login
# --------------------------------------------------------------------------

def login(driver: webdriver.Chrome, username: str, password: str) -> bool:
    driver.get(LOGIN_URL)
    wait = WebDriverWait(driver, 20)

    try:
        user_field = wait.until(EC.presence_of_element_located((By.ID, "userName")))
        pass_field = driver.find_element(By.ID, "password")
    except TimeoutException:
        print("Login form did not load in time.")
        return False

    user_field.clear()
    user_field.send_keys(username)
    pass_field.clear()
    pass_field.send_keys(password)

    # The "Log In" button is a plain <button type="button">, so we click it
    # rather than submitting the form directly - this lets the site's own
    # JS run its normal login/XSRF handshake.
    try:
        login_btn = driver.find_element(
            By.XPATH, "//button[normalize-space(text())='Log In']"
        )
    except NoSuchElementException:
        login_btn = driver.find_element(By.ID, "d2l_1_5_318")

    login_btn.click()

    # Wait until we've navigated away from the login page.
    try:
        wait.until(lambda d: "/d2l/login" not in d.current_url)
    except TimeoutException:
        pass

    time.sleep(1.5)  # let any final client-side redirect settle

    if "/d2l/login" in driver.current_url:
        return False

    return True


# --------------------------------------------------------------------------
# Quiz list page -> attempt links
# --------------------------------------------------------------------------

def get_attempt_links(driver: webdriver.Chrome, course_code: str, qi: int) -> List[str]:
    url = (f"{BASE_URL}/d2l/lms/quizzing/user/quiz_submissions.d2l"
           f"?qi={qi}&ou={course_code}")
    driver.get(url)

    wait = WebDriverWait(driver, 20)
    try:
        wait.until(EC.presence_of_element_located((By.PARTIAL_LINK_TEXT, "Attempt")))
    except TimeoutException:
        # Might genuinely have zero attempts, or session dropped - caller
        # will decide based on whether userName field re-appears.
        pass

    if driver.find_elements(By.ID, "userName"):
        raise RuntimeError("Session appears to be logged out while fetching "
                            f"quiz list page (qi={qi}). Login may have expired.")

    soup = BeautifulSoup(driver.page_source, "html.parser")

    links = []
    for a in soup.find_all("a", href=True):
        if re.match(r"^\s*Attempt\s+\d+\s*$", a.get_text()):
            href = a["href"]
            links.append(href if href.startswith("http") else BASE_URL + href)

    return links


# --------------------------------------------------------------------------
# Attempt review page -> questions
# --------------------------------------------------------------------------

def clean_html_fragment(raw_html: str) -> str:
    if raw_html is None:
        return ""
    unescaped = html_lib.unescape(raw_html)
    text = BeautifulSoup(unescaped, "html.parser").get_text(separator=" ")
    return re.sub(r"\s+", " ", text).strip()


def parse_attempt_html(page_html: str, source_label: str) -> List[Question]:
    soup = BeautifulSoup(page_html, "html.parser")
    questions: List[Question] = []

    anchors = soup.find_all("a", id=re.compile(r"^Q\d+$"))

    for anchor in anchors:
        options_table = anchor.find_next("table", class_="d_t")
        if options_table is None:
            continue

        question_text = None
        for el in anchor.next_elements:
            if el is options_table:
                break
            if getattr(el, "name", None) == "d2l-html-block" and not el.has_attr("inline"):
                question_text = clean_html_fragment(el.get("html", ""))
                break

        if not question_text:
            continue

        options: List[Option] = []
        for row in options_table.find_all("tr"):
            html_block = row.find("d2l-html-block", attrs={"inline": True})
            if html_block is None:
                continue
            option_text = clean_html_fragment(html_block.get("html", ""))
            if not option_text:
                continue

            is_correct = bool(
                row.find(attrs={"alt": "Correct Response"}) or
                row.find(attrs={"alt": "Correct Answer"})
            )
            options.append(Option(text=option_text, correct=is_correct))

        if options:
            questions.append(Question(text=question_text, options=options, source=source_label))

    return questions


def parse_attempt_page(driver: webdriver.Chrome, url: str, source_label: str) -> List[Question]:
    driver.get(url)
    wait = WebDriverWait(driver, 20)
    try:
        wait.until(EC.presence_of_element_located((By.CSS_SELECTOR, "a[id^='Q']")))
    except TimeoutException:
        pass

    if driver.find_elements(By.ID, "userName"):
        raise RuntimeError("Session appears to be logged out while fetching "
                            f"attempt page: {url}")

    return parse_attempt_html(driver.page_source, source_label)


# --------------------------------------------------------------------------
# PDF generation
# --------------------------------------------------------------------------

class Bullet(Flowable):
    """Draws a small empty or filled circle, used as an answer-option marker."""

    def __init__(self, filled: bool = False, diameter: float = 3.2 * mm * 0.6):
        super().__init__()
        self.filled = filled
        self.diameter = diameter
        self.width = diameter + 2
        self.height = diameter + 2

    def draw(self):
        c = self.canv
        r = self.diameter / 2
        cx = self.width / 2
        cy = self.height / 2
        c.setLineWidth(1)
        c.setStrokeColor(colors.black)
        if self.filled:
            c.setFillColor(colors.black)
            c.circle(cx, cy, r, stroke=1, fill=1)
        else:
            c.setFillColor(colors.white)
            c.circle(cx, cy, r, stroke=1, fill=0)


def build_pdf(questions: List[Question], output_path: str, title: str):
    styles = getSampleStyleSheet()
    q_style = ParagraphStyle(
        "QuestionStyle", parent=styles["Heading3"],
        spaceBefore=14, spaceAfter=6, fontSize=11.5, leading=15,
    )
    opt_style = ParagraphStyle(
        "OptionStyle", parent=styles["BodyText"],
        fontSize=10.5, leading=14,
    )
    opt_style_correct = ParagraphStyle(
        "OptionStyleCorrect", parent=opt_style,
        textColor=colors.HexColor("#1a7f37"), fontName="Helvetica-Bold",
    )
    title_style = styles["Title"]
    source_style = ParagraphStyle(
        "SourceStyle", parent=styles["BodyText"],
        fontSize=8, textColor=colors.grey, spaceAfter=2,
    )

    doc = SimpleDocTemplate(
        output_path, pagesize=LETTER,
        topMargin=20 * mm, bottomMargin=20 * mm,
        leftMargin=20 * mm, rightMargin=20 * mm,
    )

    story = []
    story.append(Paragraph(title, title_style))
    story.append(Spacer(1, 4 * mm))
    story.append(Paragraph(
        f"Compiled from {len(questions)} unique question(s). "
        "Filled circle (\u25cf) marks the correct answer.",
        styles["Normal"]
    ))
    story.append(Spacer(1, 6 * mm))

    for idx, q in enumerate(questions, start=1):
        block = []
        block.append(Paragraph(f"{idx}. {q.text}", q_style))
        block.append(Paragraph(f"Source: {q.source}", source_style))

        rows = []
        for opt in q.options:
            style = opt_style_correct if opt.correct else opt_style
            rows.append([Bullet(filled=opt.correct), Paragraph(opt.text, style)])

        opt_table = Table(rows, colWidths=[7 * mm, None])
        opt_table.setStyle(TableStyle([
            ("VALIGN", (0, 0), (-1, -1), "TOP"),
            ("LEFTPADDING", (0, 0), (-1, -1), 0),
            ("RIGHTPADDING", (0, 0), (-1, -1), 4),
            ("TOPPADDING", (0, 0), (-1, -1), 2),
            ("BOTTOMPADDING", (0, 0), (-1, -1), 2),
        ]))
        block.append(opt_table)
        story.append(KeepTogether(block))

    doc.build(story)


# --------------------------------------------------------------------------
# Main
# --------------------------------------------------------------------------

def parse_week_selection(raw: str) -> List[int]:
    raw = raw.strip().lower()
    if raw in ("all", "*"):
        return list(range(1, 9))
    weeks = []
    for part in raw.split(","):
        part = part.strip()
        if not part:
            continue
        w = int(part)
        if not (1 <= w <= 8):
            raise ValueError(f"Week number {w} is out of range (1-8).")
        weeks.append(w)
    if not weeks:
        raise ValueError("No valid week number(s) entered.")
    return weeks


def main():
    print("=== UoPeople Attempted Quiz Scraper ===\n")
    username = input("Username (UoPeople ID): ").strip()
    password = getpass.getpass("Password: ")
    course_code = input("Course code (the 'ou' number, e.g. 8455): ").strip()
    week_raw = input("Week number(s) [1-8, comma separated, or 'all']: ").strip()

    try:
        weeks = parse_week_selection(week_raw)
    except ValueError as e:
        print(f"Error: {e}")
        sys.exit(1)

    print("\nLaunching browser...")
    try:
        driver = make_driver(headless=False)
    except Exception as e:
        print(f"Could not start Chrome via Selenium: {e}")
        print("Make sure Google Chrome is installed on this machine.")
        sys.exit(1)

    all_questions: List[Question] = []
    seen_keys = set()

    try:
        print("Logging in...")
        if not login(driver, username, password):
            print("Login failed. Please check your username and password.")
            sys.exit(1)
        print("Login successful.\n")

        for week in weeks:
            qi = WEEK1_QI + (week - 1)
            print(f"Week {week} (qi={qi}): fetching attempted quizzes list...")
            try:
                attempt_links = get_attempt_links(driver, course_code, qi)
            except Exception as e:
                print(f"  Could not read quiz list for week {week}: {e}")
                continue

            if not attempt_links:
                print(f"  No attempts found for week {week}.")
                continue

            print(f"  Found {len(attempt_links)} attempt(s).")

            for i, link in enumerate(attempt_links, start=1):
                label = f"Week {week} - Attempt {i}"
                print(f"    Scraping {label}...")
                try:
                    qs = parse_attempt_page(driver, link, label)
                except Exception as e:
                    print(f"      Failed to parse {label}: {e}")
                    continue

                for q in qs:
                    key = q.dedup_key()
                    if key in seen_keys:
                        continue
                    seen_keys.add(key)
                    all_questions.append(q)
    finally:
        driver.quit()

    if not all_questions:
        print("\nNo questions were collected. Nothing to save.")
        sys.exit(0)

    output_path = "uopeople_quiz_compilation.pdf"
    week_desc = ", ".join(str(w) for w in weeks)
    title = f"Course {course_code} - Week(s) {week_desc} - Quiz Question Compilation"

    print(f"\nCollected {len(all_questions)} unique question(s). Building PDF...")
    build_pdf(all_questions, output_path, title)
    print(f"Saved: {output_path}")


if __name__ == "__main__":
    main()
