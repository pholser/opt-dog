include "globals.mzn";

/* ========== */
/* Parameters */
/* ========== */

/* The groups of dog breeds */
enum GROUP;

/* How conformation is performed for a dog */
enum CONFORMATION_PLATFORM;

/* Types of events */
enum EVENT_TYPE;

/* A dog's breed-judging status */
enum AKC_STATUS;

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


var BREEDS: b;
var EVENTS: ev;
var JUDGES: j;
solve minimize b + ev + j;
