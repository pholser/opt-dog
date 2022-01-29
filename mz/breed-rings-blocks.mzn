include "globals.mzn";

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
  Ground,
  Table,
  Ramp
};

/* Types of events */
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
array[BREEDS] of GROUP: breed_group;
array[BREEDS] of CONFORMATION_PLATFORM: breed_conformation_platform;

/* The events that are being conformation-judged */
int: number_of_events;
set of int: EVENTS = 1..number_of_events;
array[EVENTS] of EVENT_TYPE: event_type;

/* The conformation judges */
int: number_of_judges;
set of int: JUDGES = 1..number_of_judges;

/* The rings available to hold the judging in */
int: number_of_rings;
set of int: RINGS = 1..number_of_rings;

/* Rough factor for time spent per dog in breed ring */
int: minutes_per_dog;

/* Maximum minute of time */
int: max_time;
set of int: TIME = 1..max_time;

/* Lunch available during these times */
TIME: lunch_start;
TIME: lunch_end;
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
      "Need exactly one judge for event \(e)"
    )
  );
array[JUDGES] of set of EVENTS: judge_events = [
  {e | e in EVENTS where event_judge[e, j] == 1}
    | j in JUDGES
];

/* All dogs that are registered in at least one event */
int: number_of_dogs;
set of int: DOGS = 1..number_of_dogs;
array[DOGS] of BREEDS: dog_breed;
array[DOGS] of AKC_STATUS: dog_akc_status;

/* The exhibitors of the dogs */
int: number_of_exhibitors;
set of int: EXHIBITORS = 1..number_of_exhibitors;
array[EXHIBITORS] of 0..1: exhibitor_registered_for_conflict_minimization;

/* What exhibitors are showing what dogs in what events */
array[EVENTS, DOGS, EXHIBITORS] of 0..1: exhibitor_for_dog_in_event;
constraint
  forall (e in EVENTS, d in DOGS) (
    assert(
      sum (x in EXHIBITORS) (exhibitor_for_dog_in_event[e, d, x]) <= 1,
      "Need no more than one exhibitor for dog \(d)"
        ++ " in event \(e)"
    )
  );
array[DOGS] of set of EVENTS: dog_events = [
  {e | e in EVENTS, x in EXHIBITORS
    where exhibitor_for_dog_in_event[e, d, x] == 1
  }
  | d in DOGS
];
array[EXHIBITORS] of set of EVENTS: exhibitor_events = [
  {e | e in EVENTS, d in DOGS
    where exhibitor_for_dog_in_event[e, d, x] == 1
  }
  | x in EXHIBITORS
];

/* Based on number and type of dog entries, duration of events */
array[EVENTS] of int: event_duration_class = [
  minutes_per_dog
  *
  sum (d in DOGS, x in EXHIBITORS) (
    exhibitor_for_dog_in_event[e, d, x]
  )
  | e in EVENTS
];
array[EVENTS] of int: event_duration_champion = [
  minutes_per_dog
  *
  sum (
    d in DOGS where dog_akc_status[d] == Champion,
    x in EXHIBITORS) (

    exhibitor_for_dog_in_event[e, d, x]
  )
  | e in EVENTS
];

/* ========= */
/* Decisions */
/* ========= */

/*
 We want to schedule a judge's events in one or more blocks of time,
 each not to exceed some maximum, say, two hours. This provides natural
 breaks in time. Longer blocks would leave exhibitors later on in the ring
 less certain about their start times. 
 
 We want to start blocks of judge time on a five-minute boundary.

 The events in a block, however theey are ordered, are considered to run
 back-to-back. A block's start time thus induces the start times of the
 events in the block, by virtue of ordering and the estimated durations of
 the events.
 
 We will thus need to decide on a partition of each judge's events into
 one or more blocks, the ordering of the events within each blocks, and
 
 */
/* Is event held in a given ring? */
array[EVENTS, RINGS] of var 0..1: event_held_in_ring;

/* When to start each event */
array[EVENTS] of var TIME: event_start_time;

solve satisfy;

function var int: dog_event_start_time(DOGS: d, EVENTS: v)
  =
  if dog_akc_status[d] == Class
  then event_start_time[v]
  else event_start_time[v]
    + event_duration_class[v]
    - event_duration_champion[v]
  endif;

function var int: dog_event_duration(DOGS: d, EVENTS: v)
  =
  if dog_akc_status[d] == Class
  then event_duration_class[v]
  else event_duration_champion[v]
  endif;

/* Amount of event overlap for events in different rings */
array[DOGS, EVENTS, EVENTS] of var int: dog_event_overlap =
  array3d(
    DOGS,
    EVENTS,
    EVENTS,
    [ if v1 < v2
        /\ v1 in dog_events[d]
        /\ v2 in dog_events[d]
      then
        let {
          var int: start1 = dog_event_start_time(d, v1),
          var int: start2 = dog_event_start_time(d, v2),
          var int: end1 = start1 + dog_event_duration(d, v1),
          var int: end2 = start2 + dog_event_duration(d, v2)
        }
        in max(0, min(end1, end2) - max(start1, start2))
      else 0
      endif
      | d in DOGS,
        v1 in EVENTS,
        v2 in EVENTS
    ]);
var int: total_dog_overlap =
  sum (d in DOGS, v1 in EVENTS, v2 in EVENTS) (
    dog_event_overlap[d, v1, v2]
  );


/* =========== */
/* Constraints */
/* =========== */

/* An event is held in exactly one ring */

/* Use each ring at least once */

/* Events scheduled for a ring must not overlap in time */

/* The events a judge is set to judge must not overlap in time */
