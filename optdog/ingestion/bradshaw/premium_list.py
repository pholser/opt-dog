from bs4 import BeautifulSoup
from optdog.domain.premium_list import PremiumList


def extract_best_in_show_judge(assignments_table):
    pass


def extract_from_table(assignments_table):
    premium_list = PremiumList()
    premium_list.add_best_in_show_event(extract_best_in_show_judge(assignments_table))
    best_in_show_cell = assignments_table.find('b', string='BEST in SHOW')


def extract_from_page(soup):
    assignments_heading = soup.find('h2', string="Judges' Assignments")
    if assignments_heading is not None:
        assignments_table = assignments_heading.find_next_sibling('table')
        return extract_from_table(assignments_table)
    return None


def ingest(filename):
    with open(filename) as f:
        soup = BeautifulSoup(filename)

    return extract_from_page(soup)
