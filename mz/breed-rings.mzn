/* ========== */
/* Parameters */
/* ========== */

/* The groups of dog breeds */
enum GROUP = {
  Herding,
  Hound,
  NonSporting,
  Sporting,
  Terrier,
  Toy,
  Working,
  Miscellaneous
};

/* How conformation is performed for a dog */
enum CONFORMATION_POSITION = {
  Floor,
  Table,
  Ramp
};

/* The breeds that can be conformation-judged */
int: number_of_breeds;
set of int: BREEDS = 1..number_of_breeds;
array[BREEDS] of string: breed_name;
array[BREEDS] of GROUP: breed_group;
array[BREEDS] of CONFORMATION_POSITION: breed_conformation_position;

/* The events that are being conformation-judged */
int: number_of_events;
set of int: EVENTS = 1..number_of_events;
array[EVENTS] of string: event_name;

/* The dogs registered */
int: number_of_dogs;
set of int: DOGS = 1..number_of_dogs;
array[DOGS] of BREEDS: dog_breed;
array[DOGS] of string: dog_name;

/* The exhibitors of the dogs */
int: number_of_exhibitors;
set of int: EXHIBITORS = 1..number_of_exhibitors;
array[EXHIBITORS] of string: exhibitor_name;
array[EXHIBITORS] of 0..1: exhibitor_registered_for_conflict_minimization;

/* The conformation judges */
int: number_of_judges;
set of int: JUDGES = 1..number_of_judges;
array[JUDGES] of string: judge_name;

/* What judges are judging what events */
array[EVENTS] of JUDGES: judge_of_event;

/* Rough factor for time spent per dog in breed ring */
int: minutes_per_dog;

/* Maximum minute of time */
int: max_time;
set of int: TIME = 1..max_time;

/* The rings available to hold the judging in */
int: number_of_rings;
set of int: RINGS = 1..number_of_rings;
