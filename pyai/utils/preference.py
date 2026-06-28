import random
import ai2thor

PREFERENCES = {
    "Box": {
        "Floor": 1.0,
        "Shelf": 0.8,
        "Desk": 0.6,
        "CoffeeTable": 0.4
    },

    "Book": {
        "Shelf": 0.8,
        "Desk": 0.9,
        "CoffeeTable": 0.7,
        "SideTable": 0.6,
        "DiningTable": 1.0
    },

    "Pen": {
        "Drawer": 1.0,
        "Desk": 0.9,
        "SideTable": 0.6,
        "DiningTable": 0.4
    },

    "Pencil": {
        "Drawer": 1.0,
        "Desk": 0.9,
        "SideTable": 0.6,
        "DiningTable": 0.4
    },

    "Laptop": {
        "Desk": 1.0,
        "DiningTable": 0.8,
        "CoffeeTable": 0.5,
        "SideTable": 0.4
    },

    "DebitCard": {
        "Drawer": 1.0,
        "Desk": 0.7,
        "SideTable": 0.5
    },

    "CreditCard": {
        "Drawer": 1.0,
        "Desk": 0.7,
        "SideTable": 0.5
    },

    "Newspaper": {
        "CoffeeTable": 1.0,
        "SideTable": 0.8,
        "DiningTable": 0.6,
        "Desk": 0.4
    },

    "Plate": {
        "Cabinet": 1.0,
        "DiningTable": 0.8,
        "CounterTop": 0.6,
        "Sink": 0.3
    },

    "Cup": {
        "Cabinet": 1.0,
        "DiningTable": 0.8,
        "CounterTop": 0.7,
        "CoffeeTable": 0.5
    },

    "Mug": {
        "Cabinet": 1.0,
        "CoffeeMachine": 0.9,
        "CounterTop": 0.8,
        "DiningTable": 0.7,
        "CoffeeTable": 0.5
    },

    "Bowl": {
        "Cabinet": 1.0,
        "CounterTop": 0.8,
        "DiningTable": 0.7,
        "CoffeeTable": 0.5
    },

    "RemoteControl": {
        "TVStand": 1.0,
        "CoffeeTable": 0.9,
        "SideTable": 0.7,
        "Sofa": 0.3
    },

    "TissueBox": {
        "CoffeeTable": 1.0,
        "SideTable": 0.9,
        "Desk": 0.7,
        "DiningTable": 0.5
    },

    "Pillow": {
        "Bed": 1.0,
        "Sofa": 0.9,
        "ArmChair": 0.7
    },

    "Statue": {
        "Shelf": 1.0,
        "SideTable": 0.8,
        "CoffeeTable": 0.6,
        "Desk": 0.5
    },

    "Vase": {
        "DiningTable": 1.0,
        "SideTable": 0.9,
        "CoffeeTable": 0.8,
        "CounterTop": 0.5,
        "Shelf": 0.4
    },

    "Watch": {
        "Drawer": 1.0,
        "Desk": 0.8,
        "SideTable": 0.7,
        "Dresser": 0.6
    },
    "KeyChain":      {"Drawer": 1.0,
        "Desk": 0.8,
        "SideTable": 0.7,
        "Dresser": 0.6}
}
DEFAULT_PREFERENCE = {"DiningTable":1.0,
                      "SideTable":0.6,
                      "CoffeeTable":0.8,
                      "Cabinet":0.3}

def get_preferred_location(obj, objects):
    obj_type = obj["objectType"]
    preferred_dict = PREFERENCES.get(obj_type, DEFAULT_PREFERENCE)
    available_receptacles={
        o["objectType"]
        for o in objects
        if o.get("receptacle", False)
  }
    sorted_preference=sorted(preferred_dict.items(),
                             key=lambda item:item[1],
                             reverse=True)
    for preferred, score in sorted_preference:
        if preferred in available_receptacles:
            return preferred
    return None