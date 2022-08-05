# Dog Show Scheduling

## Introduction

Breeders, owners, and exhibitors ("handlers") often pay entry fees for
multiple dogs for any given AKC-sanctioned show. A single dog might be
entered in both conformation and performance (e.g. rally, obedience,
dock diving, barn hunt, agility) events. A breeder or owner may be
interested in acquiring as many titles as possible on their dogs, in order
to highlight the quality of their breeding stock. A handler may want
to exhibit many dogs in a given group of shows, so that they can earn more
in compensation and increase their own visibility and profile.

Via a *premium list* typically issued 6-8 weeks prior to a show, potential
entrants learn the dates of the show, events offered, and what judges will
judge what events. Entrants are interested in exhibiting the dogs to judges
who have given past favorable results to their dogs or dogs like theirs.

However, currently entrants pay to compete without prior knowledge of the
actual schedule of events. Typically, 3-4 days after show entries are closed,
the show superintendent publishes a *judging program* detailing (estimated)
event start times. If the judging program induces scheduling conflicts for
a particular dog or exhibitor, the exhibitor must make other arrangements
for the dog to be shown in an event, skip an event outright (basically
forfeiting the entry fee), or dash like mad to try to keep the schedule.
The potential for such conflicts could create a disincentive for multiple
entries for a given dog or exhibitor, at a time in history when dog show
entry counts are in decline.

We propose that if at registration time the registrant associates an
exhibitor code for the exhibitor that will show the dog entering the event,
we should be able to formulate a judging program that obeys the usual
constraints and minimizes time conflicts for those exhibitors. This document
describes our understanding of how a day's worth of a dog show works,
and how to formulate the creation of a judging program for a given day
as an optimization problem to minimize time conflicts. We will also
consider other objectives and constraints that parties other than
multi-exhibitors may want in a breed judging schedule.


## Assumptions

Our analysis and modeling rely upon the following assumptions and data.

[AKC Scheduling Best Practices](http://images.akc.org/pdf/Scheduling_Best_practices.pdf)
[See "Scheduling Rings" here](http://images.akc.org/pdf/RESHOW.pdf)


### Breed judging program info

* There will be one breed judging event per breed of dog that has at
  least one conformation entry for the day.
* Every breed recognized by the AKC belongs to one of the following
  groups: Herding, Hound, Non-Sporting, Sporting, Terrier, Toy,
  Working, Miscellaneous.
* The winners of breed judging events move on to compete in their
  respective group breed judging events. Miscellaneous group does not
  have a group breed judging event.
* The winners of the group breed judging events move on to compete in the
  best-in-show breed judging event.
* A particular breed judging event starts by judging "class dogs"
  (non-AKC-champions). These are puppy classes, open classes,
  bred-by-exhibitor classes, etc. Then, the best of the classes winners
  join the champions in the best-of-breed/best-of-variety judging.


### Parameters

* The overall breeding judging for a day has a fixed start time.
  The model doesn't particularly care what that start time is (8 a.m.,
  8:30 a.m., or what have you): it will represent that start time as
  "time 1" or some such.
* Let BG be the set of breed groups: {Herding, Hound, NonSporting,
  Sporting, Terrier, Toy, Working, Miscellaneous}.
* Let CP be the set of conformation platforms: {Ground, Table, Ramp}.
* Let B = [1..b] be the set of breeds of dogs entered.
  * Every breed in B belongs to a single breed group in BG.
* Let confirmation_platform_for be an array of elements of CP,
  indexed by breeds from B, where conformation_platform_for[b]
  is the designated primary conformation platform to be used in the
  breed judging event for breed B. Either a judge will express their
  preference for platform when there are options for a particular
  breed, or the platform will be the single platform option for a
  breed.
* Let AKCS be the set of AKC conformation status: {Class, Champion}.
* Let V = [1..b] be the set of breed judging events, one per breed/variety
  in B.
* Let G = [1..g] be the set of group breed judging events, one per
  group of dog entered in BG.
  * We will assume that all the group breed judging events are held
    in the same ring/rings, consecutively; followed by BIS in the
    same ring/rings. We may consider these events to be "in more than
    one ring" if the plan is to convert two or more adjacent rings
    into one large ring for group.
* We will assume that each breed judging event (breed, group, or
  best-in-show) lasts two minutes per dog entered, regardless of
  breed, classes, etc. We reserve the right to parameterize time
  per dog shown based on other factors (time of day, breed, ring
  accommodations, breed/group/BIS, judge/sweeps etc.) at a later time.
  AKC recommends assuming 25 dogs/hr (2.4 min/dog) for non-
  futurity/sweepstakes/permit judges ... these judges are 3 min/dog.
  We may adjust the model later to know about the judges' statuses
  and assign them a judging rate. Sometimes a club may know a
  particular judges' estimated rate. We will attempt to accommodate
  these rate allocations in the model at a later time.
* Assert that each event in V and G is assigned exactly one judge,
  along with BIS.
* Let D = [1..d] be the set of dogs entered in their respective breed
  judging events.
  * Assert that a dog is entered only in the breed judging event
    corresponding to their breed/variety.
  * Each dog is designated as a class dog or a champion dog w/r/t
    their breed, i.e. is assigned a member of AKCS.
* Let X = [1..x] be the set of exhibitors assigned to dogs in D
  in the breed judging events in V.
* Let J = [1..j] be the set of judges assigned to judge breed judging
  events, group breed judging events, and BIS breed judging events.
  * We will assume that for every event, the judge assigned to it
    is authorized by the AKC to judge the event. The model will not
    assert these requirements.
* Let R = [1..r] be the number of rings available to hold events in.
  * We will assume that all rings are effectively identical, so that
    each can accommodate judging of any breed/variety. We reserve the
    right to change the model in the future to account for rings
    of different sizes, that may be able to accommodate only breeds
    smaller than a given size. This would require us to assign each
    breed/variety a size class, and each ring a maximum-size class.
  * We know for each ring in R how many rings "away" it is from each
    other ring. Rings that share an edge are one ring away from each
    other. Rings that share a corner are two rings away from each other.
    Otherwise two rings are a "Manhattan distance" away from each other.
    A ring is zero rings away from itself.
  * 

TODO: "Within a judging assignment, all breeds judged on a table should be scheduled
consecutively, and all breeds judged on a ramp should be scheduled consecutively"
  ... does this mean a judging assignment overall, or a judging assignment period
      (block)?

### Decisions


### Objectives

* End the overall breed judging as early as possible -- that is,
  minimize the start time of best-in-show.


### Constraints

