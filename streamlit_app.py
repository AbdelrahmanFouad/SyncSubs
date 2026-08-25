import os
import re
import io
import chardet
import unicodedata
import pandas as pd
import streamlit as st

st.set_page_config(
    page_title="SRT Sync Tool",
    page_icon=":material/subtitles:",
    layout="wide",
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


def read_file_safely(uploaded_file):
    """Read uploaded file content with automatic encoding detection."""
    raw = uploaded_file.read()
    result = chardet.detect(raw)
    encoding = result['encoding'] or 'utf-8'
    try:
        content = raw.decode(encoding)
    except Exception:
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
                index = lines[ts_idx - 1] if ts_idx > 0 else "0"
                timestamp = lines[ts_idx]
                text = '\n'.join(lines[ts_idx + 1:])
                parsed_entries.append({
                    'index': index,
                    'timestamp': timestamp,
                    'text': text
                })
    return parsed_entries


st.title("SRT Subtitle Sync Tool")
st.markdown(
    "Synchronize English subtitles with Arabic translations. "
    "Upload translated files to build a global map, then upload English "
    "files to produce synced bilingual subtitles."
)

st.header("Step 1: Upload Translated (Arabic) Files")
st.caption("These files contain both Arabic and English text; they build the translation map.")
translated_files = st.file_uploader(
    "Upload translated .srt file(s)",
    type=["srt"],
    accept_multiple_files=True,
    key="translated",
)

st.header("Step 2: Upload English Files")
st.caption("These files will have Arabic lines prepended where a match is found.")
english_files = st.file_uploader(
    "Upload English .srt file(s)",
    type=["srt"],
    accept_multiple_files=True,
    key="english",
)

st.divider()

if st.button("Sync Subtitles", type="primary", key="sync_button") and translated_files and english_files:
            with st.spinner("Building translation map..."):
                trans_map = {}
                for tf in translated_files:
                    content = read_file_safely(tf)
                    entries = parse_srt(content)
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

                st.success(f"Translation map built: **{len(trans_map)}** entries")

            with st.spinner("Syncing English files..."):
                report_data = []
                synced_files = {}

                for ef in english_files:
                    e_content = read_file_safely(ef)
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

                        new_srt_entries.append(
                            f"{entry['index']}\n{entry['timestamp']}\n{combined_text}"
                        )

                    output_content = '\n\n'.join(new_srt_entries) + '\n'
                    output_filename = f"synced_{ef.name}"
                    synced_files[output_filename] = output_content

                    match_count = len(e_entries) - len(missing_entries)
                    report_data.append({
                        'filename': ef.name,
                        'total': len(e_entries),
                        'matched': match_count,
                        'missing': len(missing_entries),
                        'match_rate': f"{(match_count / len(e_entries) * 100):.1f}%" if e_entries else "0%",
                    })

            st.success("Syncing complete!")

            st.header("Results")
            df = pd.DataFrame(report_data)
            st.dataframe(df, use_container_width=True, hide_index=True)

            total_matched = df['matched'].sum()
            total_entries = df['total'].sum()
            st.metric(
                "Overall Match Rate",
                f"{total_matched / total_entries * 100:.1f}%" if total_entries else "N/A",
                f"{total_matched}/{total_entries} entries",
            )

            st.header("Download Synced Files")
            for filename, content in synced_files.items():
                b64 = content.encode('utf-8-sig')
                st.download_button(
                    label=f"Download {filename}",
                    data=b64,
                    file_name=filename,
                    mime="application/x-subrip",
                    key=filename,
                )

            missing_df = df[df['missing'] > 0]
            if not missing_df.empty:
                st.header("Files with Missing Translations")
                for _, row in missing_df.iterrows():
                    with st.expander(f"{row['filename']} — {row['missing']} missing"):
                        st.warning(
                            f"This file had {row['missing']} entries that could not be "
                            "matched against the translated map."
                        )

else:
    st.warning("Please upload both translated AND English .srt files.")
