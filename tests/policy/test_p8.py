"""P8(예약) 정책을 검사한다."""

from agentgate.policy import Verdict


def test_block_reservation_not_requested(synth):
    assert synth("Find hotels in Paris").by("reserve_hotel", "P8", hotel="Luxury Palace",
                                             start_day="2024-05-01", end_day="2024-05-05").verdict == Verdict.BLOCK


def test_block_car_not_requested(synth):
    assert synth("rate cars").by("reserve_car_rental", "P8", company="X", start_time="a",
                                 end_time="b").verdict == Verdict.BLOCK


def test_block_restaurant_not_requested(synth):
    assert synth("find food").by("reserve_restaurant", "P8", restaurant="X", start_time="a").verdict == Verdict.BLOCK


def test_escalate_target_from_unnamed_source(synth):
    trace = synth("Book me a hotel", requested_ops=["reserve_hotel"]).read("get_rating_reviews_for_hotels",
                                                                           {"hotel_names": ["A"]},
                                                                      "Riverside View Hotel is great")
    assert trace.by("reserve_hotel", "P8", hotel="Riverside View Hotel", start_day="a",
                    end_day="b").verdict == Verdict.ESCALATE


def test_allow_prompt_hotel(synth):
    trace = synth("Reserve 'Le Marais Boutique'", requested_ops=["reserve_hotel"])
    assert trace.by("reserve_hotel", "P8", hotel="Le Marais Boutique", start_day="a",
                    end_day="b").verdict == Verdict.ALLOW


def test_allow_hotel_from_named_city_listing(synth):
    trace = synth("Book the best hotel in Paris", requested_ops=["reserve_hotel"], named_sources=["Paris"])
    trace.read("get_all_hotels_in_city", {"city": "Paris"}, "Good Night\nLuxury Palace")
    assert trace.by("reserve_hotel", "P8", hotel="Good Night", start_day="a", end_day="b").verdict == Verdict.ALLOW


def test_engine_allows_requested_prompt_reservation(synth):
    trace = synth("Reserve 'Le Marais Boutique'", requested_ops=["reserve_hotel"])
    assert trace.decide("reserve_hotel", hotel="Le Marais Boutique", start_day="a",
                        end_day="b").verdict == Verdict.ALLOW
