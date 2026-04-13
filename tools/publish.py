#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""
Eugene's Archives — Universal Publisher
Parses TXT/MD files into HTML chapter fragments for the reader.

Usage:
  python publish.py --input article.txt --id my-article --title "My Article" --author "Eugene & Aeliss" --cover cover.jpg [--lang en]

What it does:
  1. Reads input file
  2. Strips Medium.com artifacts (headers, "X min read", "Sign up", links)
  3. Splits into chapters/sections by headers or dividers
  4. Converts to HTML fragments
  5. Writes to works/<id>/<lang>/*.html
  6. Generates works/<id>/manifest.json
  7. Copies cover image to images/
  8. Updates works.json catalog
"""

import argparse
import json
import os
import re
import shutil
import sys

BASE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


def clean_medium_artifacts(text):
    """Remove Medium.com export artifacts."""
    lines = text.split('\n')
    cleaned = []
    skip_header = True  # Skip initial Medium header block

    for i, line in enumerate(lines):
        s = line.strip()

        # Skip Medium header artifacts
        if skip_header:
            if any(pat in s.lower() for pat in [
                'min read', 'sign up', 'sign in', 'member-only',
                'follow', 'listen', 'share', 'responses',
                'press enter or click to view image',
                'more from', 'recommended from',
            ]):
                continue
            if s == '' and i < 20:
                continue
            if re.match(r'^\d+$', s):  # Standalone numbers (clap counts etc)
                continue
            if s.startswith('·') or s.startswith('--'):
                if i < 20:
                    continue

            # First real content line
            if s and not any(pat in s.lower() for pat in ['min read', 'sign up', 'follow']):
                skip_header = False

        # Always skip these anywhere
        if any(pat in s.lower() for pat in [
            'press enter or click to view image',
            'sign up to discover',
            'sign in',
            'member-only story',
            'free for a limited time',
        ]):
            continue

        # Skip Medium metadata lines
        if re.match(r'^Elyssov$', s):
            continue
        if re.match(r'^\d+ min read$', s):
            continue

        cleaned.append(line)

    return '\n'.join(cleaned)


def split_into_sections(text, force_single=False):
    """Split text into sections by headers or major dividers."""
    if force_single:
        return [('full', text)]

    lines = text.split('\n')
    sections = []
    current_title = None
    current_lines = []

    for line in lines:
        s = line.strip()

        # Detect section breaks
        is_header = False

        # Markdown headers
        if re.match(r'^#{1,3}\s+', s):
            is_header = True

        # ALL CAPS headers (common in these articles)
        if s and len(s) > 5 and s == s.upper() and not s.startswith('(') and not s.startswith('[') and len(s) < 200:
            # Check it's not a shout in the middle of text
            if not any(c in s for c in '.?!,;:') or s.endswith(':'):
                is_header = True

        # "Part X:" or "Act X:" or "Chapter X:" patterns
        if re.match(r'^(Part|Act|Chapter|Section|Часть|Глава)\s+\w+', s, re.IGNORECASE):
            is_header = True

        if is_header and current_lines:
            # Save previous section
            content = '\n'.join(current_lines)
            if content.strip():
                sections.append((current_title or f'section_{len(sections)+1}', content))
            current_lines = [line]
            # Clean title
            title = re.sub(r'^#{1,3}\s+', '', s).strip()
            current_title = title
        else:
            current_lines.append(line)

    # Last section
    if current_lines:
        content = '\n'.join(current_lines)
        if content.strip():
            sections.append((current_title or f'section_{len(sections)+1}', content))

    # If only 1-2 sections, keep as single
    if len(sections) <= 2:
        return [('full', text)]

    return sections


def text_to_html(text):
    """Convert plain text/markdown to HTML."""
    lines = text.split('\n')
    out = []
    para = []
    in_quote = False

    def flush():
        if para:
            text_joined = ' '.join(para)
            text_joined = process_inline(text_joined)
            out.append(f'<p>{text_joined}</p>')
            para.clear()

    def process_inline(t):
        # Bold **text**
        t = re.sub(r'\*\*(.+?)\*\*', r'<strong>\1</strong>', t)
        # Italic *text*
        t = re.sub(r'(?<!\*)\*([^*]+?)\*(?!\*)', r'<em>\1</em>', t)
        # Links [text](url) — keep text, drop url for reader
        t = re.sub(r'\[([^\]]+)\]\([^)]+\)', r'\1', t)
        # Inline URLs — just leave as text
        return t

    for line in lines:
        s = line.strip()

        # Empty line
        if s == '':
            flush()
            continue

        # Horizontal rules
        if s in ['---', '***', '* * *', '————————————————————————————————————————', '~~~~'] or re.match(r'^[—–\-=*~]{5,}$', s):
            flush()
            out.append('<hr class="section-break">')
            continue

        # Headers
        if s.startswith('### '):
            flush()
            out.append(f'<h3>{process_inline(s[4:])}</h3>')
            continue
        if s.startswith('## '):
            flush()
            out.append(f'<h2>{process_inline(s[3:])}</h2>')
            continue
        if s.startswith('# '):
            flush()
            out.append(f'<h1>{process_inline(s[2:])}</h1>')
            continue

        # Aeliss/Translator notes in parens
        if (s.startswith('(Translator') or s.startswith('(Aeliss') or
            s.startswith('[A.]') or s.startswith('[Aeliss') or
            s.startswith('(A.:') or s.startswith('(El:') or
            s.startswith("(Translator's note:")):
            flush()
            # Collect multi-line note
            note = s
            out.append(f'<div class="authors-note"><p>{process_inline(note)}</p></div>')
            continue

        # Author's note
        if s.startswith('*(Author') or s.startswith('*(Примечание'):
            flush()
            note = s.strip('*').strip('(').rstrip(')').rstrip('*')
            out.append(f'<div class="authors-note"><p><em>{process_inline(note)}</em></p></div>')
            continue

        # Bullet lists
        if s.startswith('- ') or s.startswith('• '):
            flush()
            out.append(f'<li>{process_inline(s[2:])}</li>')
            continue

        # Numbered items
        m = re.match(r'^(\d+)[.)]\s+(.*)', s)
        if m:
            flush()
            out.append(f'<p class="numbered"><span class="num">{m.group(1)}.</span> {process_inline(m.group(2))}</p>')
            continue

        # Regular text
        para.append(s)

    flush()

    # Wrap consecutive <li> in <ul>
    html = '\n'.join(out)
    html = re.sub(r'((?:<li>.*?</li>\n?)+)', r'<ul>\1</ul>', html)

    return html


def publish(input_file, work_id, title, author, cover_file=None, lang='en',
            subtitle='', date='2026', description='', category='article'):
    """Full publish pipeline."""

    # Read input
    with open(input_file, 'r', encoding='utf-8') as f:
        raw = f.read()

    # Clean Medium artifacts
    text = clean_medium_artifacts(raw)

    # Split into sections
    sections = split_into_sections(text)

    # Create output dirs
    work_dir = os.path.join(BASE, 'works', work_id, lang)
    os.makedirs(work_dir, exist_ok=True)

    # Generate HTML for each section
    manifest_chapters = []
    for i, (sec_title, sec_text) in enumerate(sections):
        html = text_to_html(sec_text)
        if len(sections) == 1:
            filename = 'full.html'
            ch_id = 'full'
        else:
            filename = f'section_{i+1:02d}.html'
            ch_id = f'section_{i+1:02d}'

        filepath = os.path.join(work_dir, filename)
        with open(filepath, 'w', encoding='utf-8') as f:
            f.write(html)

        entry = {
            'id': ch_id,
            'title': sec_title,
            'file': f'works/{work_id}/{lang}/{filename}'
        }
        manifest_chapters.append(entry)
        print(f'  {filename}: {len(html):,} bytes — {sec_title[:60]}')

    # Write manifest
    manifest = {
        'id': work_id,
        'title': title,
        'subtitle': subtitle,
        'author': author,
        'date': date,
        'description': description,
        'category': category,
        'lang': lang,
        'chapters': manifest_chapters,
        'cover': f'images/{work_id}.jpg' if cover_file else None
    }

    manifest_path = os.path.join(BASE, 'works', work_id, f'manifest_{lang}.json')
    with open(manifest_path, 'w', encoding='utf-8') as f:
        json.dump(manifest, f, ensure_ascii=False, indent=2)

    # Copy cover
    if cover_file and os.path.exists(cover_file):
        img_dir = os.path.join(BASE, 'images')
        os.makedirs(img_dir, exist_ok=True)
        ext = os.path.splitext(cover_file)[1]
        dest = os.path.join(img_dir, f'{work_id}{ext}')
        shutil.copy2(cover_file, dest)
        print(f'  Cover: {dest}')

    print(f'\nPublished: {work_id} ({lang}) — {len(manifest_chapters)} sections')
    return manifest


def update_works_json(manifest):
    """Add/update entry in the global works.json catalog."""
    catalog_path = os.path.join(BASE, 'works.json')

    if os.path.exists(catalog_path):
        with open(catalog_path, 'r', encoding='utf-8') as f:
            catalog = json.load(f)
    else:
        catalog = []

    # Update or add
    found = False
    for i, w in enumerate(catalog):
        if w['id'] == manifest['id']:
            # Merge language
            if manifest['lang'] not in w.get('languages', []):
                w.setdefault('languages', ['en']).append(manifest['lang'])
            catalog[i] = {**w, **{k: v for k, v in manifest.items() if k != 'chapters'}}
            found = True
            break

    if not found:
        catalog.append({
            'id': manifest['id'],
            'title': manifest['title'],
            'subtitle': manifest.get('subtitle', ''),
            'author': manifest['author'],
            'date': manifest.get('date', '2026'),
            'description': manifest.get('description', ''),
            'category': manifest.get('category', 'article'),
            'cover': manifest.get('cover'),
            'languages': [manifest['lang']],
        })

    with open(catalog_path, 'w', encoding='utf-8') as f:
        json.dump(catalog, f, ensure_ascii=False, indent=2)

    print(f'Catalog updated: {len(catalog)} works')


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description="Eugene's Archives Publisher")
    parser.add_argument('--input', '-i', required=True, help='Input TXT/MD file')
    parser.add_argument('--id', required=True, help='Work ID (slug)')
    parser.add_argument('--title', '-t', required=True, help='Title')
    parser.add_argument('--author', '-a', default='Eugene Lyssovsky & Aeliss', help='Author')
    parser.add_argument('--cover', '-c', help='Cover image file')
    parser.add_argument('--lang', '-l', default='en', help='Language (en/ru)')
    parser.add_argument('--subtitle', '-s', default='', help='Subtitle')
    parser.add_argument('--date', '-d', default='2026', help='Date')
    parser.add_argument('--description', default='', help='Short description')
    parser.add_argument('--category', default='article', help='Category (article/book/paper/sacred)')

    args = parser.parse_args()

    manifest = publish(
        args.input, args.id, args.title, args.author,
        args.cover, args.lang, args.subtitle, args.date,
        args.description, args.category
    )
    update_works_json(manifest)
