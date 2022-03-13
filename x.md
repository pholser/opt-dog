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
as an optimization problem to minimize time conflicts.


## Assumptions

Our analysis and modeling rely upon the following assumptions and data.

[AKC Scheduling Best Practices](http://images.akc.org/pdf/Scheduling_Best_practices.pdf)
[See "Scheduling Rings" here](http://images.akc.org/pdf/RESHOW.pdf)


### Modeling a breed judging program

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
* We will assume that each breed judging event lasts two minutes
  per dog entered.
* Schedule the breed judging events in blocks of time not to exceed
  60 minutes, . The events within a block have the same judge, and are
  held in the same ring, starting one after the other. Events 