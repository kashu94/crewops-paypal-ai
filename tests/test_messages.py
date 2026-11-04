def test_several_photos_become_one_description_of_at_most_three_sentences():
    from crewpay.ops.messages import summarise_photos

    photos = [
        "Dent on the left engine cowling. It is about a hand wide.",
        "The registration VT-KFB is painted on the rear fuselage.",
        "Dent on the left engine cowling. Seen from below.",
        "Scratch along the cargo door.",
    ]
    text = summarise_photos("VT-KFB", photos, None)
    assert text.count(".") <= 3
    assert text.startswith("Dent on the left engine cowling.") and "VT-KFB" in text
    assert text.count("Dent on the left engine cowling") == 1
    assert summarise_photos("G3", [], None) == ""
