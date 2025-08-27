# Travel map graph for AoT world
# Each node: {direction: (destination, explores_required)}
TRAVEL_MAP = {
    "Orvud": {
        "East": ("Krolva", 200),
        "Northeast": ("Decision_NE_Orvud", 750),
        "Southeast": ("Decision_SE_Orvud", 750),
    },
    "Krolva": {
        "East": ("Mitras", 200),
        "Northeast": ("Decision_NE_Krolva", 650),
        "Southeast": ("Decision_SE_Krolva", 650),
        "West": ("Orvud", 200),
    },
    "Mitras": {
        "East": ("Royal Capital", 300),
        "West": ("Krolva", 200),
        "Northeast": ("Decision_NE_Mitras", 650),
        "Southeast": ("Decision_SE_Mitras", 650),
    },
    "Royal Capital": {
        "South": ("Mitras", 300),
        "West": ("Mitras", 300),
        "East": ("Stohess", 300),
        "North": ("Utopia", 400),
    },
    "Utopia": {
        "South": ("Mitras", 300),
        "Southwest": ("Decision_SW_Utopia", 750),
        "North": ("Karanes", 300),
    },
    "Karanes": {
        "South": ("Royal Capital", 400),
        "West": ("Utopia", 300),
        "Southwest": ("Decision_SW_Karanes", 750),
    },
    "Stohess": {
        "West": ("Mitras", 200),
        "South": ("Trost", 250),
    },
    "Trost": {
        "South": ("Shiganshina", 250),
        "West": ("Ehrmich", 300),
        "North": ("Stohess", 250),
    },
    "Shiganshina": {
        "North": ("Trost", 250),
        "Northeast": ("Decision_NE_Shiganshina", 750),
        "Northwest": ("Decision_NW_Shiganshina", 750),
    },
    "Ehrmich": {
        "North": ("Mitras", 300),
        "Northeast": ("Decision_NE_Ehrmich", 750),
        "Northwest": ("Decision_NW_Ehrmich", 750),
        "East": ("Trost", 300),
    },
    # Decision points
    "Decision_NE_Orvud": {
        "Right": ("Utopia", 250),
        "Straight": ("Karanes", 250),
    },
    "Decision_SE_Orvud": {
        "Right": ("Shiganshina", 250),
        "Straight": ("Ehrmich", 250),
    },
    "Decision_NE_Krolva": {
        "Right": ("Utopia", 250),
        "Left": ("Karanes", 250),
        "Straight": ("Mitras", 250),
    },
    "Decision_SE_Krolva": {
        "Right": ("Shiganshina", 250),
        "Left": ("Ehrmich", 250),
        "Straight": ("Mitras", 200),
    },
    "Decision_NE_Mitras": {
        "Right": ("Utopia", 250),
        "Straight": ("Karanes", 250),
    },
    "Decision_SE_Mitras": {
        "Right": ("Shiganshina", 250),
        "Straight": ("Ehrmich", 250),
    },
    "Decision_SW_Utopia": {
        "Right": ("Ehrmich", 250),
        "Straight": ("Shiganshina", 250),
    },
    "Decision_SW_Karanes": {
        "Right": ("Ehrmich", 250),
        "Straight": ("Shiganshina", 250),
    },
    "Decision_NE_Shiganshina": {
        "Right": ("Stohess", 250),
        "Straight": ("Karanes", 250),
    },
    "Decision_NW_Shiganshina": {
        "Right": ("Krolva", 250),
        "Straight": ("Orvud", 250),
    },
    "Decision_NE_Ehrmich": {
        "Right": ("Stohess", 250),
        "Straight": ("Utopia", 250),
    },
    "Decision_NW_Ehrmich": {
        "Right": ("Krolva", 250),
        "Straight": ("Orvud", 250),
    },
}
