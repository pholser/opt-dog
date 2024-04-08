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


def extract_best_in_show_event(premium_list, assignments_table):
    premium_list.add_best_in_show_event(
        extract_best_in_show_judge(assignments_table)
    )


def extract_breed_events(premium_list, assignments_table, breed_group_regex, breed_group):
    breeds_cell = assignments_table.find('b', string=re.compile(breed_group_regex))
    if breeds_cell is not None:
        premium_list.add_breed_group_event(
            breed_group,
            extract_group_judge_starting_at(breeds_cell)
        )
    top_breeds_row = breeds_cell.find_parent('tr')
    if top_breeds_row is not None:
        breed_row = top_breeds_row.find_next_sibling('tr')
        while breed_row is not None:
            if breed_row.find('b', string=re.compile('(?i)breeds')):
                break
            if breed_row.find('td', {'colspan': True}):
                breed_row = breed_row.find_next_sibling('tr')
                continue
            if breed_row is not None:
                breed, judge = extract_breed_row(breed_row)
                conformation_platforms = []
                premium_list.add_breed_event(
                    Breed(breed, conformation_platforms, breed_group),
                    judge
                )
                # if it's the next breed, awesome. loop.
                # if it's a tr with a td whose colspan is 2, skip it and read the next
                # if it's a tr with a b with the string BREEDS in it, stop with this group
                breed_row = breed_row.find_next_sibling('tr')


def extract_from_table(assignments_table):
    premium_list = PremiumList()

    extract_best_in_show_event(premium_list, assignments_table)
    extract_breed_events(premium_list, assignments_table, '(?i)^sporting breeds', Group.Sporting)
    extract_breed_events(premium_list, assignments_table, '(?i)^hound breeds', Group.Hound)
    extract_breed_events(premium_list, assignments_table, '(?i)^working breeds', Group.Working)
    extract_breed_events(premium_list, assignments_table, '(?i)^terrier breeds', Group.Terrier)
    extract_breed_events(premium_list, assignments_table, '(?i)^toy breeds', Group.Toy)
    extract_breed_events(premium_list, assignments_table, '(?i)^non-sporting breeds', Group.NonSporting)
    extract_breed_events(premium_list, assignments_table, '(?i)^herding breeds', Group.Herding)

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


if __name__ == '__main__':
    premium_list = ingest(
        '/Users/pholser/py/opt-dog/docs/palm-springs-2024-premium-list.html'
    )

