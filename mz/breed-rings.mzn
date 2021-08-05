int: number_of_dogs;
set of int: DOGS = 1..number_of_dogs;

int: number_of_breeds;
set of int: BREEDS = 1..number_of_breeds;

array[DOGS] of BREEDS: breed_of_dog;

int: number_of_rings_available;
set of int: RINGS = 1..number_of_rings_available;

int: number_of_exhibitors;
set of int: EXHIBITORS = 1..number_of_exhibitors;

array[DOGS] of EXHIBITORS: exhibitor_of_dog;

int: number_of_judges;
set of int: JUDGES = 1..number_of_judges;

array[BREEDS] of JUDGES: judge_of_breed;

int: minutes_per_dog;
