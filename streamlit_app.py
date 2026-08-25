import os
import re
import io
import chardet
import unicodedata
import pandas as pd
import streamlit as st

st.set_page_config(
    page_title="SRT Subtitle Sync Tool",
    page_icon=":material/subtitles:",
    layout="wide",
    initial_sidebar_state="expanded",
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


def format_report(report_data):
    """Format the detailed report like the original script."""
    lines = []
    lines.append("")
    lines.append(" " * 30 + "DETAILED MISSING TRANSLATIONS REPORT")
    lines.append("=" * 60)
    lines.append("")

    total_matched = 0
    total_entries = 0

    for file_info in report_data:
        filename = file_info['filename']
        matched = file_info['matched']
        total = file_info['total']
        missing = file_info['missing']
        total_matched += matched
        total_entries += total

        lines.append(f"FILE: {filename}")
        lines.append(f"Summary: {matched} matched / {total} total.")
        lines.append("")

        if missing:
            lines.append("Missing Entries:")
            for m in missing:
                clean_text = m['text'].replace('\n', ' ')
                lines.append(f"  #{m['index']} [{m['timestamp']}] English: {clean_text}")
        else:
            lines.append("All entries matched!")
        lines.append("-" * 40 + "")

    lines.append("")
    lines.append("=" * 60)
    lines.append(f"OVERALL: {total_matched} matched / {total_entries} total entries")
    lines.append("=" * 60 + "")
    lines.append("")

    return "\n".join(lines)


def main():
    st.title("SRT Subtitle Sync Tool")
    st.markdown(
        "Synchronize English subtitles with Arabic translations. "
        "Upload translated files to build a global map, then upload English "
        "files to produce synced bilingual subtitles."
    )

    with st.sidebar:
        st.header("Instructions")
        st.markdown(
            "1. **Upload translated (.srt files)** - These should contain both "
            "Arabic and English text to build the translation map.\n\n"
            "2. **Upload English (.srt files)** - These will have Arabic lines "
            "prepended where a match is found in the translation map.\n\n"
            "3. Click **Sync Subtitles** to process and generate output files.\n\n"
            "4. Download synced files and detailed report."
        )
        st.divider()
        st.caption("Built with Streamlit")

    st.header("Step 1: Upload Translated (Arabic) Files")
    st.caption("These files contain both Arabic and English text; they build the translation map.")
    translated_files = st.file_uploader(
        "Upload translated .srt file(s)",
        type=["srt"],
        accept_multiple_files=True,
        key="translated",
        help="Upload one or more translated subtitle files",
    )

    st.header("Step 2: Upload English Files")
    st.caption("These files will have Arabic lines prepended where a match is found.")
    english_files = st.file_uploader(
        "Upload English .srt file(s)",
        type=["srt"],
        accept_multiple_files=True,
        key="english",
        help="Upload one or more English subtitle files to sync",
    )

    st.divider()

    if st.button("Sync Subtitles", type="primary", key="sync_button") and translated_files and english_files:
        # Build translation map
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

        # Sync English files
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
                    'missing': missing_entries,
                    'match_rate': f"{(match_count / len(e_entries) * 100):.1f}%" if e_entries else "0%",
                })

            st.success("Syncing complete!")

        # Display results
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

        # Download synced files
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

        # Download detailed report
        report_text = format_report(report_data)
        b64_report = report_text.encode('utf-8')
        st.download_button(
            label="📄 Download Detailed Report",
            data=b64_report,
            file_name="sync_report.txt",
            mime="text/plain",
            help="Download the detailed missing translations report (same format as original script)",
        )

        # Show files with missing translations
        missing_df = df[df['missing'] > 0]
        if not missing_df.empty:
            st.header("Files with Missing Translations")
            for _, row in missing_df.iterrows():
                with st.expander(f"{row['filename']} — {row['missing']} missing"):
                    st.warning(
                        f"This file had **{row['missing']}** entries "
                        "that could not be matched against the translated map."
                    )

    elif st.button("Sync Subtitles", type="primary"):
        st.warning("Please upload both translated AND English .srt files.")


if __name__ == "__main__":
    main()