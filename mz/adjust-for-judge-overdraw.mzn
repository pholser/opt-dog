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
array[JUDGES] of string: judge_name;

/* Judge assignments to breeds on each day */
array[BREEDS, DAYS] of JUDGES: original_judge_assignments_by_breed_and_day;

/* What breeds are judges authorized to judge */
% array[JUDGES, BREEDS] of 0..1: judge_can_judge_breed;

/* Upper limit on number of dogs a judge can judge on any given day */
array[JUDGES, DAYS] of int: max_dogs_for_judge_on_day;

solve satisfy;

/* =========== */
/* Data sanity */
/* =========== */

/* If a judge is not available on a given day, then they are assigned
   to judge no breeds on that day. */

/* Every breed on every day has exactly one judgee assigned. */

/* The judge assigned to judge a given breed on a given day
   is authorized to judge that breed. */

/* If no judge on any day is assigned more than their stated maximum
   for that day, we'll assume the current assignment of judges to breeds
   on each day is okay. No need to correct for overdraws. So, first off,
   let us check that there are overdraw conditions before solving for
   no-overdraw. */
  