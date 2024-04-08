from bs4 import BeautifulSoup
import re


def extract_from_table(program_table):
    counts = {}
    for row in program_table.find_all('tr'):
        breed_count_cell = row.find('td', string=re.compile(r'\d+-\d+'))
        if breed_count_cell is None:
            break
        print(breed_count_cell.text)
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
