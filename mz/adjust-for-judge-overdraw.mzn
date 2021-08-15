/* The breeds that can be conformation-judged */
int: number_of_breeds;
set of int: BREEDS = 1..number_of_breeds;
array[BREEDS] of string: breed_name;

/* The days on which conformation judging occurs */
int: number_of_days;
set of int: DAYS = 1..number_of_days;

/* How many dogs of each breed are to be judged on each day */
array[BREEDS, DAYS] of int: breed_count_on_day;

/* How many judges are available for the show */
int: number_of_judges;
set of int: JUDGES = 1..number_of_judges;

/* What days of the show the judges are available */
array[JUDGES, DAYS] of 0..1: judge_available_on_day;

/* What breeds are judges authorized to judge */
array[JUDGES, BREEDS] of 0..1: judge_can_judge_breed;

/* Whether judge assigned to judge a breed on a particular day */
array[JUDGES, BREEDS, DAYS] of 0..1: judge_assigned_to_breed_on_day;

/* Upper limit on number of dogs a judge can judge on any given day */
array[JUDGES, DAYS] of int: max_dogs_for_judge_on_day;


/* =========== */
/* Data sanity */
/* =========== */

/* For each day, if there are dogs of a given breed signed up to
   show, then there is a judge assigned to judge that breed on that day. */

/* If a judge is not available on a given day, then they are assigned
   to judge no breeds on that day. */

/* Every breed on every day is judged by exactly one judge. */

/* The judge assigned to judge a given breed on a given day
   is authorized to judge that breed. */

/* If no judge on any day is assigned more than their stated maximum
   for that day, we'll assume the current assignment of judges to breeds
   on each day is okay. No need to correct for overdraws. So, first off,
   let us check that there are overdraw conditions before solving for
   no-overdraw. */
  