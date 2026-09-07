"""Reduce the `<notAllowed/>` patterns of a RELAX NG schema.

Why this module exists. An ODD that imports only part of the TEI empties
model classes: no member of model.lLike left if you encode no verse, none
of model.quoteLike if you quote nothing. `odd2relax` renders those
classes as `<notAllowed/>` — the pattern that matches nothing — which is
exact, and leaves the reduction to the processor.

libxml2, on which the whole of this project's validation rests, does not
do it usefully: compiling the project schema was still running after ten
minutes, and a version that did compile rejected documents that conform
(a `<zone>` nested in a `<zone>`) because the contaminated content models
had not been reduced.

The rules applied here are those of the RELAX NG 1.0 specification,
section 4.20. They do not change the language the schema recognises: they
write differently what the schema already says.

    choice(notAllowed, p)  = p           a choice loses its impossible branch
    choice(notAllowed, ..) = notAllowed  if nothing is left
    group(notAllowed, p)   = notAllowed  the sequence demands the impossible
    interleave, oneOrMore, list, mixed   likewise
    zeroOrMore(notAllowed) = empty       zero occurrences is still allowed
    optional(notAllowed)   = empty
    element(notAllowed)    = notAllowed
    attribute(notAllowed)  = notAllowed
    except(notAllowed)     -- the exception disappears

And, once propagated to a fixed point: a `<define>` that reduces to
notAllowed makes every `<ref>` naming it impossible too.

It has no `__main__` and never had one — it is a library `build.py`
imports, and the `#!/usr/bin/env python3` it carried for two years said
otherwise.
"""

from lxml import etree

RNG = "http://relaxng.org/ns/structure/1.0"

# A composed pattern becomes impossible as soon as one of its children
# is: it needs ALL of its parts.
DEMANDING = {"group", "interleave", "oneOrMore", "list", "mixed",
             "element", "attribute"}
# A repeatable or optional pattern falls to <empty/>: zero occurrences is
# still an allowed number of occurrences.
OPTIONAL = {"zeroOrMore", "optional"}


def _name(element):
    return etree.QName(element).localname if isinstance(element.tag, str) else None


def _patterns(element):
    """The children that are patterns, minus documentation and name classes.

    `<element name="x">` carries its name in an attribute;
    `<element><anyName/>…` carries it in an element. A name class is not
    a pattern and makes nothing impossible.
    """
    for child in element:
        # <a:documentation> and its like live outside the RELAX NG
        # namespace: they are not patterns.
        if not isinstance(child.tag, str) or not child.tag.startswith(f"{{{RNG}}}"):
            continue
        if _name(child) in ("param", "except"):
            continue
        if _name(element) in ("element", "attribute") and _is_name_class(child):
            continue
        yield child


def _is_name_class(element):
    """Does *element* NAME its element rather than describe its content?

    A name class is written `<name>`, `<anyName>`, `<nsName>` — or
    `<choice>`, whose children are themselves name classes. That is the
    shape `odd2relax` gives an elementSpec with alternative names; taking
    it for a pattern deleted the element's only content, and lxml then
    refused the whole grammar.
    """
    name = _name(element)
    if name in ("name", "anyName", "nsName"):
        return True
    if name == "choice":
        children = [child for child in element if isinstance(child.tag, str)
                    and child.tag.startswith(f"{{{RNG}}}")]
        return bool(children) and all(_is_name_class(child) for child in children)
    return False


def _is_impossible(element, impossible_names):
    """Is *element* a pattern that recognises nothing?"""
    name = _name(element)
    if name == "notAllowed":
        return True
    if name == "ref":
        return element.get("name") in impossible_names
    return False


def _replace(element, by):
    """Replace *element* with a bare pattern named *by*, in place."""
    parent = element.getparent()
    fresh = etree.SubElement(parent, f"{{{RNG}}}{by}")
    parent.replace(element, fresh)
    return fresh


def _simplify_pattern(element, impossible_names):
    """One bottom-up pass over *element*. Returns the number of reductions."""
    reductions = 0
    for child in list(element):
        if isinstance(child.tag, str):
            reductions += _simplify_pattern(child, impossible_names)

    name = _name(element)
    if name is None or name in ("grammar", "div"):
        return reductions

    # A <define> or a <start> carrying several patterns is a <group>
    # (RELAX NG 1.0, 4.12): one impossible pattern makes them impossible.
    # That is the shape odd2relax gives the _sequenceRepeatable classes.
    if name in ("define", "start"):
        already = len(element) == 1 and _name(element[0]) == "notAllowed"
        if not already and any(_is_impossible(child, impossible_names)
                               for child in _patterns(element)):
            for child in list(element):
                element.remove(child)
            etree.SubElement(element, f"{{{RNG}}}notAllowed")
            return reductions + 1
        # A definition carries an implicit group too: the empty pattern
        # is absorbed there as it is elsewhere, and stopping here left it
        # in place.
        return reductions + _simplify_empties(element, name)

    # An <except> that no longer subtracts anything: the exception goes.
    if name == "except":
        if all(_is_impossible(child, impossible_names)
               for child in _patterns(element)):
            element.getparent().remove(element)
            return reductions + 1
        return reductions

    children = list(_patterns(element))
    if not children:
        return reductions

    if name == "choice":
        impossible = [child for child in children
                      if _is_impossible(child, impossible_names)]
        if impossible:
            for child in impossible:
                element.remove(child)
            reductions += len(impossible)
            left = list(_patterns(element))
            if not left:
                _replace(element, "notAllowed")
            elif len(left) == 1:
                # A choice with one branch is that branch.
                element.getparent().replace(element, left[0])
            return reductions
    elif any(_is_impossible(child, impossible_names) for child in children):
        if name in OPTIONAL:
            _replace(element, "empty")
            return reductions + 1
        if name in DEMANDING:
            _replace(element, "notAllowed")
            return reductions + 1

    return reductions + _simplify_empties(element, name)


def _simplify_empties(element, name):
    """The rules on `<empty/>` (RELAX NG 1.0, 4.19).

    Reducing the notAllowed produces them: zeroOrMore(notAllowed) becomes
    empty, and a `<zeroOrMore><empty/></zeroOrMore>` makes libxml2 reject
    documents that conform. The empty pattern is absorbed in a sequence
    and in a repetition; it is not absorbed in a choice, where it means
    "or nothing", nor in an element, where it means "with no content".
    """
    children = list(_patterns(element))
    if not children:
        return 0
    empties = [child for child in children if _name(child) == "empty"]
    if not empties:
        return 0

    # In a choice, <empty/> means "or nothing": it is not absorbed. It
    # goes only if the choice offers nothing but emptiness.
    if name == "choice":
        if len(children) > 1 and len(empties) == len(children):
            _replace(element, "empty")
            return 1
        return 0

    # Everywhere else several children are an implicit group, where the
    # empty pattern IS absorbed. That is the two-child <oneOrMore>
    # odd2relax writes inside <listPerson>.
    if len(empties) < len(children):
        for child in empties:
            element.remove(child)
        left = list(_patterns(element))
        if name in ("group", "interleave") and len(left) == 1:
            element.getparent().replace(element, left[0])
        return len(empties)

    # All empty: the sequence and the repetition reduce to empty; an
    # element or an attribute with no content stays what it is.
    if name in ("group", "interleave", "zeroOrMore", "oneOrMore", "optional"):
        _replace(element, "empty")
        return 1

    return 0


def _impossible_names(grammar):
    """The `<define>` names whose definitions are ALL impossible.

    `odd2relax` emits several `<define combine="choice">` for one name;
    the name is impossible only if none of its definitions recognises
    anything.
    """
    by_name = {}
    for define in grammar.iter(f"{{{RNG}}}define"):
        by_name.setdefault(define.get("name"), []).append(define)
    impossible = set()
    for name, definitions in by_name.items():
        empty = [len(d) == 1 and _name(d[0]) == "notAllowed" for d in definitions]
        # choice(notAllowed, p) = p: they must ALL be.
        # interleave(notAllowed, p) = notAllowed: one is enough.
        interleaved = any(d.get("combine") == "interleave" for d in definitions)
        if (any(empty) if interleaved else all(empty)):
            impossible.add(name)
    return impossible, by_name


def simplify(grammar):
    """Reduce the `<notAllowed/>` of *grammar*, in place, to a fixed point.

    Args:
        grammar (etree._Element | etree._ElementTree): the RELAX NG grammar.

    Returns:
        int: how many patterns were eliminated.
    """
    if hasattr(grammar, "getroot"):
        grammar = grammar.getroot()

    total = 0
    while True:
        impossible, by_name = _impossible_names(grammar)
        reductions = _simplify_pattern(grammar, impossible)

        # A definition that has become impossible goes, along with the
        # references naming it — which have just been reduced above.
        for name in impossible:
            for define in by_name[name]:
                define.getparent().remove(define)
                reductions += 1
        # An impossible branch of a combined define falls on its own.
        for define in list(grammar.iter(f"{{{RNG}}}define")):
            # Only a branch of a CHOICE is erased: removing an
            # interleaved branch would widen the language recognised.
            if len(define) == 1 and _name(define[0]) == "notAllowed" \
                    and define.get("combine") == "choice":
                define.getparent().remove(define)
                reductions += 1

        total += reductions
        if reductions == 0:
            return total
