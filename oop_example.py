"""Complete Python OOP example: classes, inheritance, encapsulation, and polymorphism."""

# CLASS: Employee is a reusable object blueprint for company employees.
class Employee:
    """Base class representing a company employee."""

    def __init__(self, name: str, role: str, salary: float) -> None:
        # ENCAPSULATION: internal attributes are prefixed with _ to indicate protected access.
        self._name = name
        self._role = role
        self._salary = salary

    @property
    def name(self) -> str:
        return self._name

    @property
    def role(self) -> str:
        return self._role

    @property
    def salary(self) -> float:
        return self._salary

    def work(self) -> str:
        return f"{self.name} is working as a {self.role}."

    def give_raise(self, amount: float) -> None:
        if amount <= 0:
            raise ValueError("Raise amount must be positive")
        self._salary += amount

    def __str__(self) -> str:
        return f"Employee(name={self.name}, role={self.role}, salary={self.salary:.2f})"


# INHERITANCE: Developer extends Employee and reuses its implementation.
class Developer(Employee):
    """Developer subclass with a specialized coding task."""

    def __init__(self, name: str, salary: float, language: str) -> None:
        super().__init__(name=name, role="Developer", salary=salary)
        self.language = language

    def work(self) -> str:
        return f"{self.name} is writing code in {self.language}."

    def debug(self) -> str:
        return f"{self.name} is debugging a {self.language} application."


# INHERITANCE: Manager also extends Employee and adds a team property.
class Manager(Employee):
    """Manager subclass with an additional team member list."""

    def __init__(self, name: str, salary: float, team: list[Employee] | None = None) -> None:
        super().__init__(name=name, role="Manager", salary=salary)
        self.team = team or []

    def work(self) -> str:
        # POLYMORPHISM: Manager overrides work(), different behavior than Employee and Developer.
        return f"{self.name} is managing a team of {len(self.team)} employees."

    def add_team_member(self, employee: Employee) -> None:
        self.team.append(employee)

    def list_team(self) -> str:
        if not self.team:
            return f"{self.name} has no team members yet."
        member_names = ", ".join(member.name for member in self.team)
        return f"{self.name}'s team: {member_names}."


def main() -> None:
    alice = Developer(name="Alice", salary=90000.0, language="Python")
    bob = Developer(name="Bob", salary=85000.0, language="JavaScript")
    carol = Manager(name="Carol", salary=105000.0)

    carol.add_team_member(alice)
    carol.add_team_member(bob)

    print(alice)
    print(bob)
    print(carol)
    print()

    print(alice.work())
    print(bob.debug())
    print(carol.work())
    print(carol.list_team())
    print()

    alice.give_raise(5000.0)
    carol.give_raise(7500.0)

    print("After raises:")
    print(alice)
    print(carol)


if __name__ == "__main__":
    main()
