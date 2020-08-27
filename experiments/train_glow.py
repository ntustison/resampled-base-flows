# Import required packages
import torch
import torchvision as tv
import numpy as np
import normflow as nf

from . import utils
from . import Glow

import os
import argparse


# Parse input arguments
parser = argparse.ArgumentParser(description='Train Glow model on image dataset.')

parser.add_argument('--config', type=str, default='config/glow.yaml',
                    help='Path config file specifying model architecture and training procedure')
parser.add_argument('--resume', action='store_true', help='Flag whether to resume training')
parser.add_argument('--gpu', action='store_true', help='Flag whether to use gpu is available')
#parser.add_argument('--tlimit', type=float, default=None,
#                    help='Number of hours after which to stop training')

args = parser.parse_args()


# Load config
config = utils.get_config(args.config)


# Get computing device
device = torch.device('cuda' if args.gpu and torch.cuda.is_available() else 'cpu')


# Prepare training data
batch_size = config['training']['batch_size']
class_cond = config['model']['class_cond']

# Load dataset
if config['dataset']['name'] == 'cifar10':
    if class_cond:
        config['model']['num_classes'] = 10

    if config['dataset']['transform']['type'] == 'logit':
        alpha = config['dataset']['transform']['param']
        logit = nf.utils.Logit(alpha=alpha)
        test_trans = [tv.transforms.ToTensor(), nf.utils.Jitter(),
                      logit, nf.utils.ToDevice(device)]
        train_trans = [tv.transforms.RandomHorizontalFlip()] + test_trans
        # Set parameters for bits per dim evaluation
        bpd_trans = 'logit'
        bpd_param = [alpha]
    else:
        raise NotImplementedError('The transform ' + config['dataset']['transform']['type']
                                  + 'is not implemented for ' + config['dataset']['name'])
    train_data = tv.datasets.CIFAR10(config['dataset']['path'], train=True, download=True,
                                     transform=tv.transforms.Compose(train_trans))
    train_loader = torch.utils.data.DataLoader(train_data, batch_size=batch_size,
                                               shuffle=True)

    test_data = tv.datasets.CIFAR10(config['dataset']['path'], train=False, download=True,
                                    transform=tv.transforms.Compose(test_trans))
    test_loader = torch.utils.data.DataLoader(test_data, batch_size=batch_size,
                                              shuffle=True)
else:
    raise NotImplementedError('The dataset ' + config['dataset']['name']
                              + 'is not implemented.')


# Create model
model = Glow(config)

# Move model on GPU if available
model = model.to(device)
model = model.double()


# Prepare folders for results
root = config['training']['save_root']
cp_dir = os.path.join(root, 'checkpoints')
sam_dir = os.path.join(root, 'samples')
log_dir = os.path.join(root, 'log')
# Create dirs if not existent
for dir in [cp_dir, sam_dir, log_dir]:
    if not os.path.isdir(dir):
        os.mkdir(dir)



# Prepare training utilities
max_iter = config['training']['max_iter']
cp_iter = config['training']['cp_iter']
log_iter = config['training']['log_iter']

loss_hist = np.zeros((0, 2))
bpd_hist = np.zeros((0, 5))

optimizer = torch.optim.Adam(model.parameters(), lr=config['training']['lr'],
                             weight_decay=config['training']['weight_decay'])

train_iter = iter(train_loader)
test_iter = iter(test_loader)

for it in range(max_iter):
    try:
        x, y = next(train_iter)
    except StopIteration:
        train_iter = iter(train_loader)
        x, y = next(train_iter)
    optimizer.zero_grad()
    loss = model.forward_kld(x, y.to(device) if class_cond else None)

    if ~(torch.isnan(loss) | torch.isinf(loss)):
        loss.backward()
        optimizer.step()

    loss_append = np.array([[it + 1, loss.detach().to('cpu').numpy()]])
    loss_hist = np.concatenate([loss_hist, loss_append])
    del (x, y, loss)

    if (it + 1) % log_iter:
        with torch.no_grad():
            try:
                x, y = next(train_iter)
            except StopIteration:
                train_iter = iter(train_loader)
                x, y = next(train_iter)
            b = nf.utils.bitsPerDim(model, x, y.to(device) if class_cond else None,
                                    trans=bpd_trans, trans_param=bpd_param)
            bpd_train = b.to('cpu').numpy()
            try:
                x, y = next(test_iter)
            except StopIteration:
                test_iter = iter(test_loader)
                x, y = next(test_iter)
            b = nf.utils.bitsPerDim(model, x, y.to(device) if class_cond else None,
                                    trans=bpd_trans, trans_param=bpd_param)
            bpd_test = b.to('cpu').numpy()
        bpd_append = np.array([[it + 1, np.nanmean(bpd_train), np.nanstd(bpd_train),
                                np.nanmean(bpd_test), np.nanstd(bpd_test)]])
        bpd_hist = np.concatenate([bpd_hist, bpd_append])
        np.savetxt(os.path.join(cp_dir, 'bits_per_dim.csv'), bpd_hist, delimiter=',',
                   header='it,train_mean,train_std,test_mean,test_std', comments='')
        np.savetxt(os.path.join(cp_dir, 'loss.csv'), loss_hist, delimiter=',',
                   header='it,loss', comments='')