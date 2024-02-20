from bs4 import BeautifulSoup
from optdog.domain.breed import Breed
from optdog.domain.group import Group
from optdog.domain.premium_list import PremiumList
import re


def extract_group_judge_starting_at(cell):
    return cell.next_sibling.next_sibling


def extract_breed_row(row):
    cells = row.find_all('td')
    return cells[0].text, cells[1].next


def extract_best_in_show_judge(assignments_table):
    best_in_show_cell = assignments_table.find('b', string=re.compile('(?i)best in show'))
    if best_in_show_cell is not None:
        return extract_group_judge_starting_at(best_in_show_cell)


def extract_from_table(assignments_table):
    premium_list = PremiumList()
    premium_list.add_best_in_show_event(
        extract_best_in_show_judge(assignments_table)
    )
    sporting_breeds_cell = assignments_table.find('b', string=re.compile('(?i)sporting breeds'))
    if sporting_breeds_cell is not None:
        premium_list.add_breed_group_event(
            Group.Sporting,
            extract_group_judge_starting_at(sporting_breeds_cell)
        )
    sporting_breeds_row = sporting_breeds_cell.find_parent('tr')
    if sporting_breeds_row is not None:
        breed_row = sporting_breeds_row.find_next_sibling()
        while breed_row is not None:
            breed, judge = extract_breed_row(breed_row)
            conformation_platforms = []
            premium_list.add_breed_event(
                Breed(breed, conformation_platforms, Group.Sporting),
                judge
            )
            breed_row = None
    return premium_list


def extract_from_page(soup):
    assignments_heading = soup.find('h2', string="Judges' Assignments")
    if assignments_heading is not None:
        assignments_table = assignments_heading.find_next_sibling('table')
        if assignments_table is not None:
            return extract_from_table(assignments_table)
    return None


def ingest(filename):
    with open(filename) as f:
        soup = BeautifulSoup(f, features='html.parser')
    return extract_from_page(soup)
