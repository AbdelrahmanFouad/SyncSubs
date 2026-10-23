import os
import re
import io
import difflib
import chardet
import unicodedata
from pathlib import Path

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


_ARABIC_RE = re.compile(
    r'[\u0600-\u06FF\u0750-\u077F\u08A0-\u08FF\uFB50-\uFDFF\uFE70-\uFEFF]'
)


def is_arabic(text):
    """Check if text contains Arabic characters (full block + extended + forms)."""
    return bool(_ARABIC_RE.search(text))


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


def split_entry(entry):
    """Split a block's text into (arabic_lines, english_lines)."""
    lines = entry['text'].split('\n')
    arabic_lines = [l.strip() for l in lines if is_arabic(l)]
    english_lines = [l.strip() for l in lines if not is_arabic(l)]
    return arabic_lines, english_lines


def pair_files(translated_files, english_files):
    """Pair each English file to its own translated file.

    Order: exact filename -> closest stem (difflib) -> sole translated file.
    Returns {english_filename: translated_UploadedFile} (missing keys = unpaired).
    """
    t_by_name = {tf.name: tf for tf in translated_files}
    t_names = list(t_by_name.keys())
    pairs = {}

    for ef in english_files:
        if ef.name in t_by_name:
            pairs[ef.name] = t_by_name[ef.name]
            continue
        stem = Path(ef.name).stem.lower()
        best_name, best_score = None, 0.0
        for tn in t_names:
            score = difflib.SequenceMatcher(
                a=stem, b=Path(tn).stem.lower()).ratio()
            if score > best_score:
                best_name, best_score = tn, score
        if best_name and best_score >= 0.5:
            pairs[ef.name] = t_by_name[best_name]
        elif len(t_names) == 1:
            # exactly one translated file -> it's the obvious source
            pairs[ef.name] = t_by_name[t_names[0]]
    return pairs


def sync_pair(t_entries, e_entries):
    """Sync English entries against their PAIRED translated entries.

    Pass 1 (positional): difflib sequence-alignment — an unchanged line carries
        its Arabic straight over, anchored by position. Survives insertions,
        deletions and re-ordering in a re-edited reel.
    Pass 2 (text, order-aware): any remaining English line is looked up against
        the still-unused translated lines (a list per normalized key, consumed
        in order, so repeated "Hey!"s map to their translations in sequence).

    Returns {e_index: (arabic_or_None, provenance)} where provenance is
    'position', 'text' or 'missing'.
    """
    t_pairs = []  # (norm_english, arabic)
    for e in t_entries:
        ar, en = split_entry(e)
        if ar and en:
            t_pairs.append((normalize_text('\n'.join(en)), '\n'.join(ar)))

    t_keys = [p[0] for p in t_pairs]
    e_keys = [normalize_text(e['text']) for e in e_entries]

    result = {}
    used_t = set()

    # Pass 1 — positional alignment
    if t_keys and e_keys:
        sm = difflib.SequenceMatcher(a=t_keys, b=e_keys, autojunk=False)
        for tag, i1, i2, j1, j2 in sm.get_opcodes():
            if tag == 'equal':
                for off in range(j2 - j1):
                    result[j1 + off] = (t_pairs[i1 + off][1], 'position')
                    used_t.add(i1 + off)

    # Pass 2 — order-aware text fallback from the remaining translated lines
    text_map = {}
    for i, (norm, ar) in enumerate(t_pairs):
        if i not in used_t:
            text_map.setdefault(norm, []).append(ar)
    used = {k: 0 for k in text_map}

    for j in range(len(e_entries)):
        if j in result:
            continue
        k = e_keys[j]
        bucket = text_map.get(k)
        if bucket and used[k] < len(bucket):
            result[j] = (bucket[used[k]], 'text')
            used[k] += 1
        else:
            result[j] = (None, 'missing')

    return result


def format_report(report_data, missing_details):
    """Format the detailed report."""
    lines = []
    lines.append("")
    lines.append(" " * 30 + "DETAILED MISSING TRANSLATIONS REPORT")
    lines.append("=" * 60)
    lines.append("")

    total_matched = 0
    total_entries = 0

    for file_info in report_data:
        filename = file_info['filename']
        lines.append(f"FILE: {filename}")
        lines.append(
            f"Summary: {file_info['matched']} matched "
            f"({file_info['position']} by position, {file_info['text']} by text) "
            f"/ {file_info['total']} total.")
        lines.append("")

        if missing_details[filename]:
            lines.append("Missing Entries:")
            for m in missing_details[filename]:
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
        "Carry your existing Arabic translations onto a new English cut. "
        "Each English file is paired to its own translated file (by name), "
        "matched by position first, then by text."
    )

    with st.sidebar:
        st.header("Instructions")
        st.markdown(
            "1. **Upload translated (.srt files)** — bilingual files "
            "(Arabic + English) you translated before.\n\n"
            "2. **Upload English (.srt files)** — the new/re-edited cut.\n\n"
            "3. Click **Sync Subtitles**.\n\n"
            "4. Download the synced bilingual files and the report."
        )
        st.divider()
        st.caption("Matching is per-file, position-first, then text. "
                   "No cross-file leakage.")

    st.header("Step 1: Upload Translated (Arabic) Files")
    st.caption("Bilingual files (Arabic + English) that supply the translations.")
    translated_files = st.file_uploader(
        "Upload translated .srt file(s)",
        type=["srt"],
        accept_multiple_files=True,
        key="translated",
        help="Upload one or more translated subtitle files",
    )

    st.header("Step 2: Upload English Files")
    st.caption("The new English-only cut that will receive the Arabic lines.")
    english_files = st.file_uploader(
        "Upload English .srt file(s)",
        type=["srt"],
        accept_multiple_files=True,
        key="english",
        help="Upload one or more English subtitle files to sync",
    )

    st.divider()

    if st.button("Sync Subtitles", type="primary", key="sync_button") and translated_files and english_files:
        pairs = pair_files(translated_files, english_files)

        report_data = []
        synced_files = {}
        missing_details = {}

        with st.spinner("Syncing..."):
            for ef in english_files:
                paired_tf = pairs.get(ef.name)
                e_entries = parse_srt(read_file_safely(ef))
                t_entries = parse_srt(read_file_safely(paired_tf)) if paired_tf else []

                if not paired_tf:
                    st.warning(
                        f"No translated file matched **{ef.name}** — "
                        "leaving all its entries untranslated."
                    )

                result = sync_pair(t_entries, e_entries)

                new_srt_entries = []
                missing_entries = []
                n_pos = n_text = 0

                for j, entry in enumerate(e_entries):
                    arabic, prov = result.get(j, (None, 'missing'))
                    if arabic:
                        combined_text = f"{arabic}\n{entry['text']}"
                        if prov == 'position':
                            n_pos += 1
                        else:
                            n_text += 1
                    else:
                        combined_text = entry['text']
                        missing_entries.append(entry)

                    new_srt_entries.append(
                        f"{entry['index']}\n{entry['timestamp']}\n{combined_text}"
                    )

                output_content = '\n\n'.join(new_srt_entries) + '\n'
                output_filename = f"synced_{ef.name}"
                synced_files[output_filename] = output_content

                matched = n_pos + n_text
                report_data.append({
                    'filename': ef.name,
                    'total': len(e_entries),
                    'position': n_pos,
                    'text': n_text,
                    'matched': matched,
                    'missing': len(missing_entries),
                    'match_rate': f"{(matched / len(e_entries) * 100):.1f}%" if e_entries else "0%",
                })
                missing_details[ef.name] = missing_entries

        st.success("Syncing complete!")

        # Results
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

        # Download all synced files
        st.header("Download Synced Files")
        if len(synced_files) > 1:
            import zipfile
            zip_buffer = io.BytesIO()
            with zipfile.ZipFile(zip_buffer, 'w', zipfile.ZIP_DEFLATED) as zip_file:
                for filename, content in synced_files.items():
                    zip_file.writestr(filename, content.encode('utf-8-sig'))
            zip_buffer.seek(0)
            st.download_button(
                label=f"📦 Download All Synced Files ({len(synced_files)} files) ZIP",
                data=zip_buffer,
                file_name="all_synced_files.zip",
                mime="application/zip",
                key="download_all",
            )

        for filename, content in synced_files.items():
            st.download_button(
                label=f"Download {filename}",
                data=content.encode('utf-8-sig'),
                file_name=filename,
                mime="application/x-subrip",
                key=filename,
            )

        # Detailed report
        report_text = format_report(report_data, missing_details)
        st.download_button(
            label="📄 Download Detailed Report",
            data=report_text.encode('utf-8'),
            file_name="sync_report.txt",
            mime="text/plain",
            help="Download the detailed report (position/text/missing breakdown).",
        )

        # Files with missing translations
        missing_mask = df['missing'].apply(lambda x: isinstance(x, int) and x > 0)
        missing_df = df[missing_mask]
        if not missing_df.empty:
            st.header("Files with Missing Translations")
            for _, row in missing_df.iterrows():
                with st.expander(f"{row['filename']} — {row['missing']} missing"):
                    st.warning(
                        f"**{row['missing']}** entries had no match "
                        f"({row['position']} by position, {row['text']} by text). "
                        "These stay English-only."
                    )

    else:
        st.warning("Please upload both translated AND English .srt files.")


if __name__ == "__main__":
    main()
