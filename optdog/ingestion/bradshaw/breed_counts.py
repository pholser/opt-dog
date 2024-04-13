from bs4 import BeautifulSoup
from more_itertools import partition
import re


def parse_counts(raw):
    no_parens = raw.replace('(', '').replace(')', '')
    pieces = no_parens.split('-')
    counts_by_type = {}

    others, veteran_dog_pieces = partition(lambda p: 'VD' in p, pieces)
    others = list(others)
    veteran_dog_pieces = list(veteran_dog_pieces)
    if veteran_dog_pieces:
        counts_by_type['veteran-dog'] = int(veteran_dog_pieces[0].replace('VD', ''))
        pieces = others
    others, veteran_bitch_pieces = list(partition(lambda p: 'VB' in p, pieces))
    others = list(others)
    veteran_bitch_pieces = list(veteran_bitch_pieces)
    if veteran_bitch_pieces:
        counts_by_type['veteran-bitch'] = int(veteran_bitch_pieces[0].replace('VB', ''))
        pieces = others
    if len(pieces) == 2:
        counts_by_type['class-dog'] = int(pieces[0])
        counts_by_type['class-bitch'] = int(pieces[1])
    elif len(pieces) == 4:
        counts_by_type['class-dog'] = int(pieces[0])
        counts_by_type['class-bitch'] = int(pieces[1])
        counts_by_type['champion-dog'] = int(pieces[2])
        counts_by_type['champion-bitch'] = int(pieces[3])
    else:
        print(f'Warning: could not parse counts string {raw}')
    return counts_by_type


def parse_breed_and_counts(raw):
    match = re.search(r'\d', raw)
    index = match.start()
    breed_name_piece = raw[:index].strip()
    raw_counts_piece = raw[index:].strip()
    return breed_name_piece, parse_counts(raw_counts_piece)


def extract_from_table(program_table):
    counts = {}
    for row in program_table.find_all('tr'):
        breed_count_cell = row.find('td', string=re.compile(r'\d+-\d+'))
        if breed_count_cell is not None:
            breed_name, detail_counts = parse_breed_and_counts(breed_count_cell.text)
            counts[breed_name] = detail_counts
    return counts


def extract_from_page(soup):
    judges_heading = soup.find('h2', string='List of Judges')
    if judges_heading is not None:
        program_heading = judges_heading.find_next_sibling('h2', string='Judging Program')
        if program_heading is not None:
            counts_table = program_heading.find_next_sibling('table')
            if counts_table is not None:
                return extract_from_table(counts_table)
    return None


def ingest(filename):
    with open(filename) as f:
        soup = BeautifulSoup(f, features='html.parser')
    return extract_from_page(soup)


if __name__ == '__main__':
    breed_counts = ingest(
        '/Users/pholser/py/opt-dog/docs/palm-springs-2024-breed-counts.html'
    )
    print(breed_counts)
