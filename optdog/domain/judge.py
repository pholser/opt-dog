class Judge:
    def __init__(self, name, certified_breeds):
        self.name = name
        self.certified_breeds = certified_breeds

    def may_judge_breed(self, breed):
        return breed in self.certified_breeds

    def may_judge_group(self, group):
        return True
