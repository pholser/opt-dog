/* ========== */
/* Parameters */
/* ========== */

/* The breeds that can be conformation-judged */
int: number_of_breeds;
set of int: BREEDS = 1..number_of_breeds;
array[BREEDS] of string: breed_name;

int: number_of_dogs;
set of int: DOGS = 1..number_of_dogs;
array[DOGS] of BREEDS: dog_breed;

