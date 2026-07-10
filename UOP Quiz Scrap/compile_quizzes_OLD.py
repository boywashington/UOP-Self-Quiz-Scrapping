"""
How to Use:
1. Login to New UOP Portal [https://learn.uopeople.edu/d2l/home], and navigate to the specific course home page.
2. Navigate to "Activities > Quizzes" menu
3. On the Quiz List menu, see the "Evaluation Status" column of the Self-Quiz Unit you want to scrape. 
4. Click the "On Attempt" link to open the "Quiz Submissions - Self-Quiz Unit" page.
5. Save this page as a local HTML file in the same folder as this script. Name it something like "Quiz_Unit-1_CS-4403-01.html" (or similar).

6. Open the Terminal in your IDE, and ensure your working directory in the Terminal is 
   [flashdrive] D:\\VSCode\\My_Project\\UOP Quiz Scrap
   [localdrive] C:\\Users\\[YourUserName]\\Documents\\VSCode\\My_Project\\UOP Quiz Scrap
   
7. Run the script from the Terminal in that WD:
   [PS D:\\VSCode\\My_Projects\\UOP Quiz Scrap> & d:/VSCode/data/python/python.exe "d:/VSCode/My_Projects/UOP Quiz Scrap/compile_quizzes.py]

8. During the script execution, a browser window will open. 
   Log in to your D2L portal. Once logged in, return to the Terminal and press [ENTER] to continue.

9. Once the script completes, you will find a compiled PDF file in the same folder as this script, named something like "Self_Quiz_Unit-1_Compilation_CS-4403.pdf".
"""

import asyncio
import os
import re
from bs4 import BeautifulSoup
from playwright.async_api import async_playwright
from reportlab.lib.pagesizes import letter
from reportlab.platypus import SimpleDocTemplate, Paragraph, Spacer
from reportlab.lib.styles import getSampleStyleSheet, ParagraphStyle
from reportlab.lib.enums import TA_CENTER

# Configuration Index (Your Simplified Input Base File)
INDEX_HTML = "Quiz_Unit-1_CS-4403-01.html"

def generate_dynamic_paths(filename):
    """
    Parses the simplified input HTML filename to automatically extract unit and course numbers,
    generating customized directory and PDF output strings dynamically.
    """
    # Look for patterns like 'Unit-1' or 'Unit 1'
    unit_match = re.search(r'Unit[- ]*(\d+)', filename, re.IGNORECASE)
    # Look for course codes like 'CS-4402-01' or 'CS 4402'
    course_match = re.search(r'([A-Z]{2,4})[- ]*(\d{4})', filename, re.IGNORECASE)
    
    # Extract string values or set defaults if naming layout deviates
    unit_str = f"Unit-{unit_match.group(1)}" if unit_match else "Unit-X"
    course_str = f"{course_match.group(1).upper()}-{course_match.group(2)}" if course_match else "COURSE"

    # Dynamic workspace directory path (e.g., "Unit-1_CS-4402")
    temp_html_dir = f"{unit_str}_{course_str}"
    
    # Dynamic OUTPUT_PDF target file path (e.g., "Self_Quiz_Unit-1_Compilation_CS-4402.pdf")
    output_pdf = f"Self_Quiz_{unit_str}_Compilation_{course_str}.pdf"
    
    return temp_html_dir, output_pdf

# Derive paths automatically
TEMP_HTML_DIR, OUTPUT_PDF = generate_dynamic_paths(INDEX_HTML)

print(f"--- Workspace Path Mappings ---")
print(f"Source Base File Name:       {INDEX_HTML}")
print(f"Workspace Folder Target:     {TEMP_HTML_DIR}")
print(f"Output Document Target:      {OUTPUT_PDF}")
print(f"--------------------------------\n")


def extract_links_from_index(file_path):
    """Parses the main HTML list to find attempt links and titles."""
    if not os.path.exists(file_path):
        return []
    with open(file_path, 'r', encoding='utf-8') as f:
        content = f.read()
    matches = re.findall(r'href="([^"]+)"[^>]*>(Attempt \d+)</a', content)
    return [(url.replace('&amp;', '&'), name) for url, name in matches]

async def download_raw_html_pages(quiz_attempts):
    """Automates the browser to download the inner HTML content of each quiz attempt."""
    os.makedirs(TEMP_HTML_DIR, exist_ok=True)
    
    async with async_playwright() as p:
        browser = await p.chromium.launch(headless=False)
        context = await browser.new_context()
        page = await context.new_page()

        print(f"\nNavigating to the portal. Please log in inside the opened browser window...")
        await page.goto(quiz_attempts[0][0])
        print("--> ACTION REQUIRED: Log in completely until you see your quiz details, then return here.")
        input("--> Press [ENTER] in this terminal ONCE LOGGED IN to begin raw data collection... ")

        for url, name in quiz_attempts:
            safe_name = f"{name.replace(' ', '_')}.html"
            local_path = os.path.join(TEMP_HTML_DIR, safe_name)
            
            print(f"Downloading content for {name}...")
            try:
                await page.goto(url)
                await page.wait_for_load_state("networkidle")
                html_content = await page.content()
                with open(local_path, "w", encoding="utf-8") as f:
                    f.write(html_content)
            except Exception as e:
                print(f"  Error fetching {name}: {e}")
            await asyncio.sleep(1.5)
            
        await browser.close()

def parse_and_deduplicate_questions():
    """Reads downloaded HTML files, extracts unique questions, choices, and answers using D2L block tags."""
    unique_questions = {}
    
    if not os.path.exists(TEMP_HTML_DIR):
        print(f"Error: Temporary directory '{TEMP_HTML_DIR}' does not exist.")
        return {}

    html_files = [f for f in os.listdir(TEMP_HTML_DIR) if f.endswith('.html')]
    if not html_files:
        print("No downloaded HTML files found to parse.")
        return {}

    print("\nParsing and deduplicating questions...")
    for file_name in html_files:
        path = os.path.join(TEMP_HTML_DIR, file_name)
        with open(path, 'r', encoding='utf-8', errors='ignore') as f:
            soup = BeautifulSoup(f.read(), 'html.parser')
            
        blocks = soup.find_all('d2l-html-block')
        for block in blocks:
            html_attr = block.get('html', '')
            if not html_attr:
                continue
                
            if len(html_attr) > 15:
                # Find parent text layout and look for adjacent choice components (table class='d_t')
                parent_container = block.find_parent('div', style=lambda v: v and 'text-align' in v)
                has_options = False
                option_table = None
                
                if parent_container:
                    option_table = parent_container.find_next_sibling('table', class_='d_t')
                    has_options = option_table is not None
                
                # Check for questions, explicit syntax tokens, or structural options layout (handles True/False statements)
                if has_options or '?' in html_attr or 'Which' in html_attr or 'What' in html_attr or '____' in html_attr:
                    q_text = BeautifulSoup(html_attr, 'html.parser').get_text(strip=True)
                    
                    if q_text not in unique_questions:
                        choices = []
                        correct_choice = "Not Found / Not Marked"
                        
                        if option_table:
                            rows = option_table.find_all('tr')
                            for row in rows:
                                option_block = row.find('d2l-html-block')
                                if option_block:
                                    opt_text = BeautifulSoup(option_block.get('html', ''), 'html.parser').get_text(strip=True)
                                    choices.append(opt_text)
                                    
                                    row_html = str(row)
                                    if 'Correct Response' in row_html or 'Correct Answer' in row_html or 'infRightAnswer' in row_html:
                                        correct_choice = opt_text
                        
                        unique_questions[q_text] = {
                            'choices': choices,
                            'correct_answer': correct_choice
                        }

    print(f"Extracted {len(unique_questions)} total unique questions from all attempts.")
    return unique_questions

def build_pdf_document(questions_dict, output_filename):
    """Formats the unique structured quiz data cleanly into a standard compiled study guide PDF."""
    print(f"Generating clean compiled PDF: {output_filename}")
    doc = SimpleDocTemplate(output_filename, pagesize=letter, rightMargin=40, leftMargin=40, topMargin=40, bottomMargin=40)
    story = []
    
    styles = getSampleStyleSheet()
    title_style = ParagraphStyle('TitleStyle', parent=styles['Heading1'], alignment=TA_CENTER, spaceAfter=20)
    q_style = ParagraphStyle('QStyle', parent=styles['Normal'], fontSize=11, leading=15, spaceBefore=10, spaceAfter=6, fontName='Helvetica-Bold')
    
    opt_style = ParagraphStyle('OptStyle', parent=styles['Normal'], fontSize=10, leading=14, leftIndent=20, spaceAfter=3)
    correct_opt_style = ParagraphStyle('CorrectOptStyle', parent=styles['Normal'], fontSize=10, leading=14, leftIndent=20, fontName='Helvetica-Bold', textColor='green', spaceAfter=3)
    
    # Clean up filename dashes/underscores to output a readable title layout inside the document
    clean_title = output_filename.replace(".pdf", "").replace("_", " ")
    story.append(Paragraph(f"<b>{clean_title}</b>", title_style))
    story.append(Spacer(1, 15))
    
    for i, (q_text, data) in enumerate(questions_dict.items(), 1):
        story.append(Paragraph(f"Q{i}. {q_text}", q_style))
        
        for opt in data['choices']:
            # Compare layout options with the target correct choice string
            if opt == data['correct_answer']:
                marker = "●  "
                story.append(Paragraph(f"{marker}{opt}", correct_opt_style))
            else:
                marker = "○  "
                story.append(Paragraph(f"{marker}{opt}", opt_style))
                
        story.append(Spacer(1, 10))
        
    doc.build(story)
    print("PDF Generation complete.")

async def main():
    quiz_attempts = extract_links_from_index(INDEX_HTML)
    if not quiz_attempts:
        print(f"Error: No attempt URLs parsed from '{INDEX_HTML}'. Make sure the file exists.")
        return
        
    if not os.path.exists(TEMP_HTML_DIR) or not os.listdir(TEMP_HTML_DIR):
        await download_raw_html_pages(quiz_attempts)
    else:
        print(f"Found existing raw downloads inside '{TEMP_HTML_DIR}'. Skipping live browser download...")
    
    unique_data = parse_and_deduplicate_questions()
    
    if unique_data:
        build_pdf_document(unique_data, OUTPUT_PDF)
    else:
        print("No valid question content could be isolated for compilation.")

if __name__ == "__main__":
    asyncio.run(main())