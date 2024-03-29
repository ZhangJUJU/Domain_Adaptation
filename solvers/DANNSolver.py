from __future__ import print_function, division

import sys
import time

import torch.nn as nn

from data_helpers.data_helper import *
from networks.DANN import DANN
from solvers.Solver import Solver
import torch.nn.functional as F


class DANNSolver(Solver):

    def __init__(self, dataset_type, source_domain, target_domain, cuda='cuda:0',
                 pretrained=False,
                 batch_size=32,
                 num_epochs=9999, max_iter_num=9999999, test_interval=500, test_mode=False, num_workers=2,
                 clean_log=False, lr=0.001, gamma=10, optimizer_type='SGD', use_augment = False):
        super(DANNSolver, self).__init__(
            dataset_type=dataset_type,
            source_domain=source_domain,
            target_domain=target_domain,
            cuda=cuda,
            pretrained=pretrained,
            batch_size=batch_size,
            num_epochs=num_epochs,
            max_iter_num=max_iter_num,
            test_interval=test_interval,
            test_mode=test_mode,
            num_workers=num_workers,
            clean_log=clean_log,
            lr=lr,
            gamma=gamma,
            optimizer_type=optimizer_type
        )
        self.model_name = 'DANN'
        self.iter_num = 0
        self.use_augment = use_augment

    def get_alpha(self, delta=10.0):
        if self.num_epochs != 999999:
            p = self.epoch / self.num_epochs
        else:
            p = self.iter_num / self.max_iter_num

        return np.float(2.0 / (1.0 + np.exp(-delta * p)) - 1.0)

    def set_model(self):
        if self.dataset_type == 'Digits':
            if self.task in ['MtoU', 'UtoM']:
                self.model = DANN(n_classes=self.n_classes, base_model='DigitsMU')
            if self.task in ['StoM']:
                self.model = DANN(n_classes=self.n_classes, base_model='DigitsStoM')

        if self.dataset_type in ['Office31', 'OfficeHome']:
            self.model = DANN(n_classes=self.n_classes, base_model='ResNet50')

        if self.pretrained:
            self.load_model(path=self.models_checkpoints_dir + '/' + self.model_name + '_best_train.pt')

        self.model = self.model.to(self.device)

    def test(self, data_loader):
        self.model.eval()

        corrects = 0
        data_num = len(data_loader.dataset)
        processed_num = 0

        for inputs, labels in data_loader:
            sys.stdout.write('\r{}/{}'.format(processed_num, data_num))
            sys.stdout.flush()

            inputs = inputs.to(self.device)
            labels = labels.to(self.device)

            class_outputs = self.model(inputs, test_mode=True)

            _, preds = torch.max(class_outputs, 1)

            corrects += (preds == labels.data).sum().item()
            processed_num += labels.size()[0]

        acc = corrects / processed_num
        average_loss = 0
        print('\nData size = {} , corrects = {}'.format(processed_num, corrects))

        return average_loss, acc

    def augment(self, x, T=True, A=True):
        # tmp = torch.Tensor(x) + torch.randn_like(x) * 0.1

        N = x.size(0)
        theta = np.zeros((N, 2, 3), dtype=np.float32)
        theta[:, 0, 0] = theta[:, 1, 1] = 1.0

        if T:
            theta[:, :, 2:] += np.random.uniform(low=-0.2, high=0.2, size=(N, 2, 1))

        if A:
            theta[:, :, :2] += np.random.normal(scale=0.1, size=(N, 2, 2))

        grid = F.affine_grid(theta=torch.from_numpy(theta), size=x.size())
        new_x = F.grid_sample(input=x, grid=grid)

        return new_x

    def train_one_epoch(self):
        since = time.time()
        self.model.train()

        total_loss = 0
        source_corrects = 0

        total_target_num = len(self.data_loader['target']['train'].dataset)
        processed_target_num = 0
        total_source_num = 0

        alpha = 0
        for target_inputs, target_labels in self.data_loader['target']['train']:
            sys.stdout.write('\r{}/{}'.format(processed_target_num, total_target_num))
            sys.stdout.flush()

            self.update_optimizer()

            self.optimizer.zero_grad()

            alpha = self.get_alpha()

            # TODO 1 : Target Train
            if self.use_augment:
                target_inputs = self.augment(target_inputs)
            target_inputs = target_inputs.to(self.device)
            target_domain_outputs = self.model(target_inputs, alpha=alpha, test_mode=False, is_source=False)
            target_domain_labels = torch.ones((target_labels.size(0), 1), device=self.device)
            target_domain_loss = nn.BCELoss()(target_domain_outputs, target_domain_labels)

            # TODO 2 : Source Train

            source_iter = iter(self.data_loader['source']['train'])
            source_inputs, source_labels = next(source_iter)
            if self.use_augment:
                source_inputs = self.augment(source_inputs)
            source_inputs = source_inputs.to(self.device)

            source_domain_outputs, source_class_outputs = self.model(source_inputs, alpha=alpha, test_mode=False,
                                                                     is_source=True)
            source_labels = source_labels.to(self.device)

            source_class_loss = nn.CrossEntropyLoss()(
                source_class_outputs,
                source_labels
            )

            source_domain_labels = torch.zeros((source_labels.size()[0], 1), device=self.device)
            source_domain_loss = nn.BCELoss()(source_domain_outputs, source_domain_labels)

            # TODO 3 : LOSS

            loss = target_domain_loss + source_domain_loss + source_class_loss

            loss.backward()

            self.optimizer.step()

            # TODO 5 : other parameters
            total_loss += loss.item() * source_labels.size()[0]
            _, source_class_preds = torch.max(source_class_outputs, 1)
            source_corrects += (source_class_preds == source_labels.data).sum().item()
            total_source_num += source_labels.size()[0]
            processed_target_num += target_labels.size()[0]
            self.iter_num += 1

        acc = source_corrects / total_source_num
        average_loss = total_loss / total_source_num

        print()
        print('\nData size = {} , corrects = {}'.format(total_source_num, source_corrects))
        print('Using {:4f}'.format(time.time() - since))
        print('Alpha = ', alpha)
        return average_loss, acc
