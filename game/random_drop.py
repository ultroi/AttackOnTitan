import random

def get_random_drop():
    roll = random.random()
    if roll < 0.70:
        # 70% chance for bottle or cylinder of gas
        if random.random() < 0.01:
            # 1% chance for cylinder
            return {
                'type': 'cylinder',
                'amount': 2000,
                'image': 'https://i.ibb.co/4ZnskFNq/image.jpg',
                'message': f'You found a cylinder of gas and received 2000 gas!'
            }
        else:
            gas_amount = random.randint(200, 1999)
            return {
                'type': 'bottle',
                'amount': gas_amount,
                'image': 'https://i.ibb.co/5XhB3zhB/image.jpg',
                'message': f'You found a bottle of gas and received {gas_amount} gas!'
            }
    elif roll < 0.99:
        # 29% chance for Valors
        valors = random.randint(5, 50)
        return {
            'type': 'valors',
            'amount': valors,
            'message': f'You found {valors} Valors!'
        }
    else:
        # 1% chance for Crystals
        crystals = random.randint(1, 4)
        return {
            'type': 'crystals',
            'amount': crystals,
            'message': f'You found {crystals} Crystals!'
        }
