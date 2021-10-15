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
enum CONFORMATION_PLATFORM = {
  Floor,
  Table,
  Ramp
};

/* Types of conformation events */
enum EVENT_TYPE = {
  BreedJudging,
  GroupBreedJudging,
  BestInShowBreedJudging
};

enum AKC_STATUS = {
  Class,
  Champion
};

/* The breeds that can be conformation-judged */
int: number_of_breeds;
set of int: BREEDS = 1..number_of_breeds;
array[BREEDS] of string: breed_name;
array[BREEDS] of GROUP: breed_group;
array[BREEDS] of CONFORMATION_PLATFORM: breed_conformation_platform;

/* The events that are being conformation-judged */
int: number_of_events;
set of int: EVENTS = 1..number_of_events;
array[EVENTS] of string: event_name;
array[EVENTS] of EVENT_TYPE: event_type;

/* The dogs registered */
int: number_of_dogs;
set of int: DOGS = 1..number_of_dogs;
array[DOGS] of BREEDS: dog_breed;
array[DOGS] of string: dog_name;
array[DOGS] of AKC_STATUS: dog_akc_status;

/* The exhibitors of the dogs */
int: number_of_exhibitors;
set of int: EXHIBITORS = 1..number_of_exhibitors;
array[EXHIBITORS] of string: exhibitor_name;
array[EXHIBITORS] of 0..1: exhibitor_registered_for_conflict_minimization;

/* The rings available to hold the judging in */
int: number_of_rings;
set of int: RINGS = 1..number_of_rings;

/* The conformation judges */
int: number_of_judges;
set of int: JUDGES = 1..number_of_judges;
array[JUDGES] of string: judge_name;

/* Rough factor for time spent per dog in breed ring */
int: minutes_per_dog;

/* Maximum minute of time */
int: max_time;
set of int: TIME = 1..max_time;

/* Lunch available during these times */
int: lunch_start;
int: lunch_end;
constraint
  assert(
    lunch_start < lunch_end,
    "Lunch start \(lunch_start) should be before lunch end \(lunch_end)"
  );

/* What judges are judging what events */
array[EVENTS, JUDGES] of 0..1: event_judge;
constraint
  forall (e in EVENTS) (
    assert(
      sum (j in JUDGES) (event_judge[e, j]) == 1,
      "Need exactly one judge for event \(event_name[e])"
    )
  );
array[JUDGES] of set of EVENTS: judge_events = [
  {e | e in EVENTS where event_judge[e, j] == 1}
    | j in JUDGES
];

/* What exhibitors are showing what dogs in what events */
array[DOGS, EVENTS, EXHIBITORS] of 0..1: exhibitor_for_dog_in_event;
constraint
  forall (d in DOGS, e in EVENTS) (
    assert(
      sum (x in EXHIBITORS) (exhibitor_for_dog_in_event[d, e, x]) == 1,
      "Need exactly one exhibitor for dog \(dog_name[d])"
        ++ " in event \(event_name[e])"
    )
  );
array[DOGS] of set of EVENTS: dog_events = [
  {e | e in EVENTS, x in EXHIBITORS
    where exhibitor_for_dog_in_event[d, e, x] == 1
  }
  | d in DOGS
];
array[EXHIBITORS] of set of EVENTS: exhibitor_events = [
  {e | e in EVENTS, d in DOGS
    where exhibitor_for_dog_in_event[d, e, x] == 1
  }
  | x in EXHIBITORS
];

/* Expected duration of events */
array[EVENTS] of int: event_duration_classes = [
];
