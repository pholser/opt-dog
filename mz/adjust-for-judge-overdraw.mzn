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

/* Whether a judge is available to judge on a given day */
array[JUDGES, DAYS] of 0..1: judge_available_on_day;

/* What breeds are judges authorized to judge */
/* NOTE: When we source this data from the AKC or wherever,
   note that:
   (1) Sometimes a judge will have listed, e.g. "Hound Group"
       but no specific hounds listed. Assume that judge can judge
       all breeds in that group.
   (2) Sometimes a judge will have listed, e.g. "Terrier Group"
       and specific terrier breeds listed. Assume that judge can
       judge only those breeds of that group listed.
   (3) Sometimes a judge will have listed, e.g. "Poodles" or
       "Manchester Terriers". Assume that judge can judge all
       varieties of that breed, regardless of what group the
       variety falls under.
   These are problems to reconcile when sourcing data, not
   in the model per se.
*/
array[JUDGES] of set of BREEDS: eligible_breeds_for_judge;

/* Upper limit on number of dogs a judge can judge on any given day */
array[JUDGES, DAYS] of int: upper_limit_dogs_for_judge_on_day;

array[JUDGES, DAYS] of int: judge_max_on_day =
  array2d(
    JUDGES,
    DAYS,
    [ judge_available_on_day[j, d] * upper_limit_dogs_for_judge_on_day[j, d]
      | j in JUDGES, d in DAYS ]
  );

array[JUDGES, DAYS] of int: judge_breed_count_on_day =
  array2d(
    JUDGES,
    DAYS,
    [ sum (b in BREEDS) (
        (original_judge_assignments_by_breed_and_day[b, d] == j)
        *
        breed_count_on_day[b, d]
      )
      | j in JUDGES, d in DAYS ]
  );


array[BREEDS, DAYS] of var JUDGES: adjusted_judge_assignments_by_breed_and_day;
constraint forall (b in BREEDS, d in DAYS) (
  adjusted_judge_assignments_by_breed_and_day[b, d]
  ==
  original_judge_assignments_by_breed_and_day[b, d]
);

solve satisfy;

/* =========== */
/* Data sanity */
/* =========== */

/* The judge assigned to judge a given breed on a given day
   is authorized to judge that breed. */
constraint
  forall (b in BREEDS, d in DAYS) (
    let { JUDGES: j = original_judge_assignments_by_breed_and_day[b, d] }
    in
    assert(
      b in eligible_breeds_for_judge[j],
      "Judge " ++ judge_name[j]
        ++ " is assigned to judge breed " ++ breed_name[b]
        ++ " on day " ++ show(d)
        ++ " but is not authorized to judge that breed; the judge is eligible for: "
        ++ show([ breed_name[br] | br in eligible_breeds_for_judge[j] ])
    )
  );

/* If no judge on any day is assigned more than their stated maximum
   for that day, we'll assume the current assignment of judges to breeds
   on each day is okay. No need to correct for overdraws. So, first off,
   let us check that there are overdraw conditions before solving for
   no-overdraw. */
constraint
  assert(
    sum (j in JUDGES, d in DAYS) (
      judge_breed_count_on_day[j, d] >= judge_max_on_day[j, d] + 1
    )
    >=
    1,
    "No judges are overdrawn on any of the show days"
  );
    
output([
  breed_name[b]
    ++ "\t"
    ++ concat([
         judge_name[
           fix(adjusted_judge_assignments_by_breed_and_day[b, d])
         ]
         ++ if d == number_of_days then "\n" else "\t" endif
         | d in DAYS
       ])
  | b in BREEDS
]);

output([
  judge_name[j]
    ++ "\t"
    ++ concat([
      show(judge_breed_count_on_day[j, d])
        ++ if d == number_of_days then "\n" else "\t" endif
      | d in DAYS
    ])
  | j in JUDGES
]);
