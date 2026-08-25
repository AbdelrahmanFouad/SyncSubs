import os
import re
import chardet
import logging
import sys
import unicodedata
from datetime import datetime

# --- DYNAMIC PATH HANDLING ---
if getattr(sys, 'frozen', False):
    BASE_DIR = os.path.dirname(sys.executable)
else:
    BASE_DIR = os.path.dirname(os.path.abspath(__file__))

ENGLISH_DIR = os.path.join(BASE_DIR, 'english')
TRANSLATED_DIR = os.path.join(BASE_DIR, 'transalted')
OUTPUT_DIR = os.path.join(BASE_DIR, 'done')
LOG_FILENAME = 'sync_report.log'

if not os.path.exists(OUTPUT_DIR):
    os.makedirs(OUTPUT_DIR)

log_path = os.path.join(OUTPUT_DIR, LOG_FILENAME)
logging.basicConfig(
    level=logging.INFO,
    format='%(message)s',
    handlers=[
        logging.FileHandler(log_path, mode='w', encoding='utf-8'),
        logging.StreamHandler()
    ]
)

def normalize_text(text):
    """Highly aggressive normalization for bulletproof matching."""
    if not text:
        return ""
    text = unicodedata.normalize('NFKC', text)
    text = text.lower()
    text = re.sub(r'<[^>]+>', '', text)
    replacements = {
        '’': "'", '‘': "'", '`': "'",
        '“': '"', '”': '"',
        '–': '-', '—': '-',
        '…': '...',
        '\r': '', '\n': ' '
    }
    for old, new in replacements.items():
        text = text.replace(old, new)
    text = re.sub(r'[^a-z0-9\s]', '', text)
    text = re.sub(r'\s+', ' ', text).strip()
    return text

def is_arabic(text):
    """Check if text contains Arabic characters."""
    return bool(re.search(r'[\u0600-\u06FF]', text))

def read_file_safely(file_path):
    """Read file with automatic encoding detection."""
    with open(file_path, 'rb') as f:
        raw = f.read()
    result = chardet.detect(raw)
    encoding = result['encoding'] or 'utf-8'
    try:
        content = raw.decode(encoding)
    except:
        content = raw.decode('utf-8', errors='ignore')
    if content.startswith('\ufeff'):
        content = content[1:]
    return content

def parse_srt(content):
    """Robust SRT parser."""
    content = content.replace('\r\n', '\n').strip()
    blocks = re.split(r'\n\s*\n', content)
    parsed_entries = []
    for block in blocks:
        lines = [l.strip() for l in block.split('\n') if l.strip()]
        if len(lines) >= 3:
            ts_idx = -1
            for i, line in enumerate(lines):
                if "-->" in line:
                    ts_idx = i
                    break
            if ts_idx != -1:
                index = lines[ts_idx-1] if ts_idx > 0 else "0"
                timestamp = lines[ts_idx]
                text = '\n'.join(lines[ts_idx+1:])
                parsed_entries.append({
                    'index': index,
                    'timestamp': timestamp,
                    'text': text
                })
    return parsed_entries

def build_global_map():
    """Build a mapping of English text to Arabic translations."""
    trans_map = {}
    if not os.path.exists(TRANSLATED_DIR):
        logging.error(f"Error: '{TRANSLATED_DIR}' not found.")
        return trans_map

    files = [f for f in os.listdir(TRANSLATED_DIR) if f.lower().endswith('.srt')]
    logging.info(f"Building map from {len(files)} files in '{TRANSLATED_DIR}'...")

    for filename in files:
        f_path = os.path.join(TRANSLATED_DIR, filename)
        content = read_file_safely(f_path)
        entries = parse_srt(content)
        file_count = 0
        for entry in entries:
            lines = entry['text'].split('\n')
            arabic_lines = [l.strip() for l in lines if is_arabic(l)]
            english_lines = [l.strip() for l in lines if not is_arabic(l)]
            if arabic_lines and english_lines:
                arabic_text = '\n'.join(arabic_lines)
                english_text = '\n'.join(english_lines)
                norm_eng = normalize_text(english_text)
                if norm_eng and norm_eng not in trans_map:
                    trans_map[norm_eng] = arabic_text
                    file_count += 1
        logging.info(f"  - {filename}: Added {file_count} unique translations.")
    
    logging.info(f"Total global map size: {len(trans_map)} entries.\n")
    return trans_map

def sync_files():
    """Perform the synchronization process."""
    if not os.path.exists(ENGLISH_DIR):
        logging.error(f"Error: '{ENGLISH_DIR}' not found.")
        return

    trans_map = build_global_map()
    english_files = [f for f in os.listdir(ENGLISH_DIR) if f.lower().endswith('.srt')]
    
    if not english_files:
        logging.warning(f"No .srt files found in '{ENGLISH_DIR}'.")
        return

    report_data = []

    for filename in english_files:
        logging.info(f"Processing: {filename}")
        e_path = os.path.join(ENGLISH_DIR, filename)
        e_content = read_file_safely(e_path)
        e_entries = parse_srt(e_content)
        
        new_srt_entries = []
        missing_entries = []
        
        for entry in e_entries:
            norm_eng = normalize_text(entry['text'])
            arabic = trans_map.get(norm_eng)
            
            if arabic:
                combined_text = f"{arabic}\n{entry['text']}"
            else:
                combined_text = entry['text']
                missing_entries.append(entry)
            
            new_srt_entries.append(f"{entry['index']}\n{entry['timestamp']}\n{combined_text}")
        
        output_path = os.path.join(OUTPUT_DIR, filename)
        with open(output_path, 'w', encoding='utf-8-sig') as f:
            f.write('\n\n'.join(new_srt_entries) + '\n')
        
        match_count = len(e_entries) - len(missing_entries)
        logging.info(f"  Finished: Matched {match_count}/{len(e_entries)} entries.")
        report_data.append({
            'filename': filename,
            'total': len(e_entries),
            'matched': match_count,
            'missing': missing_entries
        })

    # --- GENERATE DETAILED LOG REPORT ---
    logging.info("\n" + "="*60)
    logging.info("           DETAILED MISSING TRANSLATIONS REPORT")
    logging.info("="*60 + "\n")

    for file_info in report_data:
        logging.info(f"FILE: {file_info['filename']}")
        logging.info(f"Summary: {file_info['matched']} matched / {file_info['total']} total.")
        
        if file_info['missing']:
            logging.info("Missing Entries:")
            for m in file_info['missing']:
                clean_text = m['text'].replace('\n', ' ')
                logging.info(f"  #{m['index']} [{m['timestamp']}] English: {clean_text}")
        else:
            logging.info("All entries matched!")
        logging.info("-" * 40 + "\n")

if __name__ == "__main__":
    try:
        logging.info(f"Current Base Directory: {BASE_DIR}")
        sync_files()
        logging.info(f"\nSuccess! Log and results are in: {os.path.abspath(OUTPUT_DIR)}")
        input("\nPress Enter to exit...")
    except Exception as e:
        logging.error(f"CRITICAL ERROR: {str(e)}")
        import traceback
        logging.error(traceback.format_exc())
        input("\nAn error occurred. Press Enter to exit...")
