import itertools as iter
import more_itertools as miter


def integer_partitions(n, I=1):
    yield (n,)
    for i in range(I, n // 2 + 1):
        for p in integer_partitions(n - i, i):
            yield (i,) + p


def partition_to_indices(p):
    sum = 0
    indices = []
    for i in p:
        sum += i
        indices.append(sum)
    return indices


def slices_of(list, indices):
    slices = []
    prev_index = 0

    for index in indices:
        slices.append(list[prev_index:index])
        prev_index = index

    return frozenset(slices)


def ordered_slices_of(list):
    n = len(list)
    # TODO: find a way to not generate partitions of a permutation
    # that would have a member whose sum exceeds a threshold
    for p in integer_partitions(n):
        indices = partition_to_indices(p)
        for q in iter.permutations(list):
            yield slices_of(q, indices)


if __name__ == '__main__':
    n = 8
    x = list(range(n))
    i = miter.countable(miter.unique_everseen(ordered_slices_of(x)))
    for s in i:
        print(s)
    print(i.items_seen)
