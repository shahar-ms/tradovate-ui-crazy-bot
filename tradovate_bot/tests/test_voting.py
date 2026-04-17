from app.capture.voting import Candidate, vote


def test_vote_empty():
    r = vote([])
    assert not r.accepted
    assert r.reason == "no_valid_candidates"


def test_vote_single_candidate():
    c = Candidate(price=19234.25, confidence=80.0, recipe="gray_only", raw_text="19234.25")
    r = vote([c])
    assert r.accepted
    assert r.price == 19234.25
    assert r.recipe == "gray_only"
    assert r.agreed_count == 1


def test_vote_unanimous_agreement_picks_highest_conf():
    a = Candidate(19234.25, 70.0, "gray_only", "19234.25")
    b = Candidate(19234.25, 92.0, "otsu_threshold", "19234.25")
    r = vote([a, b])
    assert r.accepted
    assert r.price == 19234.25
    assert r.confidence == 92.0
    assert r.recipe == "otsu_threshold"
    assert r.agreed_count == 2


def test_vote_majority_wins():
    a = Candidate(19234.25, 80.0, "r1", "19234.25")
    b = Candidate(19234.25, 85.0, "r2", "19234.25")
    c = Candidate(19234.50, 90.0, "r3", "19234.50")
    r = vote([a, b, c])
    assert r.accepted
    assert r.price == 19234.25


def test_vote_tie_rejects():
    a = Candidate(19234.25, 80.0, "r1", "19234.25")
    b = Candidate(19234.50, 85.0, "r2", "19234.50")
    r = vote([a, b])
    assert not r.accepted
    assert r.reason == "candidates_disagree"
