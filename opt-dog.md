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
judge what events. Entrants are interested in showing to judges who have
given past favorable results to their dogs or dogs like theirs.

However, currently entrants pay to compete without prior knowledge of the
actual schedule of events. Typically 3-4 days after show entries are closed,
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
as an optimization problem to minimize time conflicts.


## Assumptions

Our analysis and modeling rely upon the following assumptions and data.

[AKC Scheduling Best Practices](http://images.akc.org/pdf/Scheduling_Best_practices.pdf)

### Breed judging (conformation)

Rule of thumb: A breed conformation event runs at approximately
two minutes per dog entered. (__TODO__: elaborate on this, with examples,
possibly  including concurrent specialities, sweepstakes, etc.)

We will treat designated specialties as no different from a regular
breed ring.

For a given day's conformation events, the judges for those events will
be determined ahead of time. Any overdraws (judges assigned to judge
more than 175 dogs that day) are assumed to have been resolved.

Judges typically can judge 25 dogs per hour.

Judges are required to receive a minimum of 45 minutes for rest or 
meals should their assignment exceed 5 hours of judging.

Every judge should be allowed some idle time for lunch.

In advance, we know how many rings are available to use for breed
judging.

Attempt to schedule table breeds and ramp breeds being judged in the same
ring together to decrease the movement of equipment which causes delays. 

Attempt to schedule judges in a single ring. If a judge must be scheduled
in two rings, to minimize disruption, relocate the judge during their
lunch break. 

Assign breeds with varieties to the same judge if possible; be aware of 
scheduling conflicts if this is not possible. 

The rings scheduled to be used for group judging should have fewer dogs 
assigned to them for early conversion into the larger group ring.
