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

var BREEDS: x;
solve maximize x;
